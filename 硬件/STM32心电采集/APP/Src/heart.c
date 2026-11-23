#include "heart.h"
#include "main.h"
#include "adc.h"
#include "dma.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* 单个 ADC 半缓冲区的采样点数，与对外发送块大小一致。 */
#define ADC_HALF_SAMPLES HEART_BLOCK_SAMPLES
/* 循环 DMA 总缓冲区长度：两个半区共 72 点。 */
#define ADC_TOTAL_SAMPLES (2U * HEART_BLOCK_SAMPLES)
/* 发送队列槽数，包含当前正在 DMA 发送的槽位。 */
#define TX_QUEUE_SIZE 4U
/* 每块固定252字节：36点，每点为带模式标识和校验的七字节帧。 */
#define TX_PACKET_BYTES (ADC_HALF_SAMPLES * UART_SAMPLE_FRAME_BYTES)

/* USART1 发送 DMA 句柄，在 usart.c 定义，由 Heart_Init 配置。 */
extern DMA_HandleTypeDef hdma_usart1_tx;
/* 心电模块运行与诊断状态；主循环和中断共享，应用层只读观察。 */
volatile Heart_Status heart_status;
/* DMA 使用的缓冲区必须在整个异步传输期间保持有效。 */
/* ADC DMA 循环采样缓冲区；使用静态存储，保证异步传输期间地址有效。 */
static volatile uint16_t adc_buf[ADC_TOTAL_SAMPLES];
/* 待发送二进制块；队列中的数据在对应 DMA 发送完成前不能覆盖。 */
static uint8_t tx_data[TX_QUEUE_SIZE][TX_PACKET_BYTES];
/* 每个发送槽位中有效二进制帧的总字节数。 */
static uint16_t tx_length[TX_QUEUE_SIZE];
/* tx_head 为下次入队槽位；tx_tail 为发送队首；tx_count 为已占用槽数（含正在发送块）。 */
static uint32_t tx_head, tx_tail, tx_count;
/* adc_consumed 为已处理块序号；lead_seen 累积导联脱落状态；tx_started 为当前发送起始毫秒数。 */
static uint32_t adc_consumed, lead_seen, tx_started;
/* 串口 DMA 发送占用标志：1 表示当前发送尚未由主循环回收。 */
static uint8_t tx_active;
/* 串口完成回调置 1、主循环清零的共享标志。 */
static volatile uint8_t tx_complete;

/* 模拟周期300点：360Hz / 300 = 1.2Hz，约72次/分；查表避免实时浮点运算。 */
#define SIMULATED_PERIOD_SAMPLES 300U
/* 模拟心电P/QRS/T形态的ADC码表；用于通信演示，不是标注数据库样本。 */
static const uint16_t simulated_ecg[SIMULATED_PERIOD_SAMPLES] = {
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2049U, 2049U,
    2049U, 2050U, 2051U, 2053U, 2055U, 2058U, 2061U, 2066U, 2072U, 2078U, 2086U, 2095U,
    2105U, 2115U, 2126U, 2136U, 2146U, 2154U, 2161U, 2166U, 2168U, 2168U, 2165U, 2160U,
    2153U, 2144U, 2134U, 2124U, 2113U, 2103U, 2093U, 2085U, 2077U, 2070U, 2065U, 2061U,
    2057U, 2054U, 2053U, 2051U, 2050U, 2049U, 2049U, 2049U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2047U, 2045U, 2038U, 2024U, 1998U, 1965U, 1939U, 1947U, 2014U, 2155U, 2366U,
    2625U, 2895U, 3126U, 3264U, 3272U, 3145U, 2917U, 2642U, 2374U, 2148U, 1980U, 1870U,
    1810U, 1791U, 1802U, 1834U, 1877U, 1922U, 1962U, 1994U, 2017U, 2031U, 2040U, 2044U,
    2046U, 2047U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2049U, 2049U, 2049U, 2049U, 2050U, 2050U,
    2050U, 2051U, 2052U, 2053U, 2054U, 2055U, 2056U, 2058U, 2060U, 2062U, 2064U, 2067U,
    2070U, 2074U, 2078U, 2082U, 2087U, 2093U, 2099U, 2105U, 2112U, 2119U, 2127U, 2136U,
    2144U, 2153U, 2163U, 2172U, 2181U, 2191U, 2200U, 2210U, 2218U, 2227U, 2235U, 2242U,
    2249U, 2254U, 2259U, 2263U, 2266U, 2267U, 2268U, 2267U, 2266U, 2263U, 2259U, 2254U,
    2249U, 2242U, 2235U, 2227U, 2218U, 2210U, 2200U, 2191U, 2181U, 2172U, 2163U, 2153U,
    2144U, 2136U, 2127U, 2119U, 2112U, 2105U, 2099U, 2093U, 2087U, 2082U, 2078U, 2074U,
    2070U, 2067U, 2064U, 2062U, 2060U, 2058U, 2056U, 2055U, 2054U, 2053U, 2052U, 2051U,
    2050U, 2050U, 2050U, 2049U, 2049U, 2049U, 2049U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
    2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U, 2048U,
};
/* 最近进入模拟模式后第一个有效模拟点的绝对采样位置。 */
static uint32_t simulated_origin;

/* 外设初始化成功标志；清理失败时清零，要求重新初始化。 */
static uint8_t initialized;

/* 记录心电模块错误码 code；仅写入诊断状态，由主循环处理停止与返回。 */
static void Fail(uint32_t code)
{
    heart_status.error = code;
}

/* 检查 half 指定的 ADC 半区能否读取（0 为前半区，1 为后半区）；返回 1 表示未处于 DMA 写入或待处理中断边界。 */
static uint32_t HalfIsStable(uint32_t half)
{
    /* DMA 当前剩余搬运点数，用于判断正在写入哪个半区。 */
    uint32_t remaining = __HAL_DMA_GET_COUNTER(hadc1.DMA_Handle);
    /* 边界中断待处理或 DMA 正在写入该半区时，不允许复制。 */
    if ((DMA1->ISR & (DMA_FLAG_HT1 | DMA_FLAG_TC1)) != 0U) return 0U;
    if (half == 0U) return remaining > 0U && remaining <= ADC_HALF_SAMPLES;
    return remaining > ADC_HALF_SAMPLES && remaining <= ADC_TOTAL_SAMPLES;
}

/* 在主循环中复制最新稳定的 36 点采样块并加入发送队列；队列满时丢弃新块并计数。 */
static void CollectBlock(void)
{
    /* 稳定半区的本地快照，避免格式化期间 DMA 覆盖原数据。 */
    uint16_t samples[ADC_HALF_SAMPLES];
    /* 当前采样块编码后的累计字节数，每点固定为7字节。 */
    uint16_t length = 0;
    /* 开始复制时的 DMA 完成块序号，用于检测复制期间是否跨越边界。 */
    uint32_t seq = heart_status.adc_blocks;
    /* half 为待读半区；i 为采样索引；invalid 表示导联脱落或切换隔离块。 */
    uint32_t half, i, invalid;
    /* 由采样位置生成的 8 bit 序号，丢块时保留相应跳变。 */
    uint8_t sample_sequence;
    /* 本块已生效的来源快照，编码入队后不随后续按键请求改变。 */
    uint8_t wire_mode;
    /* 线上数据：为真实ADC值或模拟值；导联脱落和切换隔离块使用0xFFFF。 */
    uint16_t wire_value;
    if (seq == adc_consumed) return;
    half = (seq - 1U) & 1U;
    if (HalfIsStable(half) == 0U) return;
    __DMB();
    for (i = 0; i < ADC_HALF_SAMPLES; ++i)
        samples[i] = adc_buf[half * ADC_HALF_SAMPLES + i];
    __DMB();
    /* 复制期间若跨越 DMA 边界，丢弃该快照，留待后续循环重试。 */
    if (HalfIsStable(half) == 0U || heart_status.adc_blocks != seq) return;
    heart_status.adc_dropped_blocks += seq - adc_consumed - 1U;
    adc_consumed = seq;
    invalid = lead_seen;
    lead_seen = heart_status.lead_off;
    if (tx_count == TX_QUEUE_SIZE)
    {
        ++heart_status.uart_dropped_blocks; /* 不覆盖排队中或正在发送的数据。 */
        return;
    }
    /* 只在有队列空间的完整块边界切换，已排队或正在发送的块保持不变。
     * 用整块0xFFFF隔离两段，触发上位机滤波和RR历史重置，不插入文本破坏帧对齐。 */
    if (heart_status.source != heart_status.requested_source)
    {
        heart_status.source = heart_status.requested_source;
        ++heart_status.source_switches;
        ++heart_status.source_guard_blocks;
        simulated_origin = seq * ADC_HALF_SAMPLES;
        invalid = 1U;
    }
    else if (heart_status.source == HEART_SOURCE_SIMULATED)
    {
        invalid = 0U; /* 模拟测试不依赖电极是否连接；LO指示灯仍跟随真实输入。 */
    }
    wire_mode = (heart_status.source == HEART_SOURCE_SIMULATED)
                ? UART_SAMPLE_MODE_SIMULATED : UART_SAMPLE_MODE_REAL;
    for (i = 0; i < ADC_HALF_SAMPLES; ++i)
    {
        /* 从本次 Heart_Start 的第 0 个采样点编号，转换为 uint8_t 后自然按 256 回绕。 */
        sample_sequence = (uint8_t)((seq - 1U) * ADC_HALF_SAMPLES + i);
        if (invalid != 0U)
            wire_value = UART_SAMPLE_INVALID_VALUE;
        else if (heart_status.source == HEART_SOURCE_SIMULATED)
            wire_value = simulated_ecg[((seq - 1U) * ADC_HALF_SAMPLES + i - simulated_origin)
                                      % SIMULATED_PERIOD_SAMPLES];
        else
            wire_value = samples[i];
        length += UART_EncodeSampleFrame(&tx_data[tx_head][length], wire_mode, sample_sequence, wire_value);
    }
    tx_length[tx_head] = length;
    tx_head = (tx_head + 1U) % TX_QUEUE_SIZE;
    ++tx_count;
}

/* 在主循环中回收已完成的发送块并启动下一次串口 DMA；发送超时或启动失败时记录错误。 */
static void ServiceUart(void)
{
    if (tx_active != 0U && tx_complete != 0U)
    {
        tx_complete = 0U;
        tx_active = 0U;
        tx_tail = (tx_tail + 1U) % TX_QUEUE_SIZE;
        --tx_count;
        ++heart_status.uart_sent_blocks;
    }
    if (tx_active != 0U)
    {
        if ((uint32_t)(HAL_GetTick() - tx_started) > 1000U) Fail(7U);
        return;
    }
    if (tx_count == 0U) return;
    tx_complete = 0U;
    tx_active = 1U;
    tx_started = HAL_GetTick();
    heart_status.uart_status = HAL_UART_Transmit_DMA(&huart1, tx_data[tx_tail], tx_length[tx_tail]);
    if (heart_status.uart_status != HAL_OK) Fail(6U);
}


/* 初始化心电采集外设及 USART1 发送 DMA；要求 HCLK/PCLK1/PCLK2 为 72/36/72 MHz；返回 HAL_OK、HAL_BUSY 或 HAL_ERROR。 */
HAL_StatusTypeDef Heart_Init(void)
{
    /* ADC 外设时钟配置：PCLK2 六分频，目标 ADC 时钟为 12 MHz。 */
    RCC_PeriphCLKInitTypeDef adc_clock = {0};
    if (heart_status.running) return HAL_BUSY;
    initialized = 0U;
    heart_status.error = 0U;
    if (HAL_RCC_GetHCLKFreq() != 72000000U ||
        HAL_RCC_GetPCLK1Freq() != 36000000U ||
        HAL_RCC_GetPCLK2Freq() != 72000000U)
    {
        Fail(8U);
        return HAL_ERROR;
    }
    adc_clock.PeriphClockSelection = RCC_PERIPHCLK_ADC;
    adc_clock.AdcClockSelection = RCC_ADCPCLK2_DIV6;
    if (HAL_RCCEx_PeriphCLKConfig(&adc_clock) != HAL_OK)
    {
        Fail(9U);
        return HAL_ERROR;
    }
    MX_GPIO_Init();
    MX_DMA_Init();
    MX_ADC1_Init();
    MX_TIM3_Init();
    MX_USART1_UART_Init();
    /* 心电模块在此启用发送 DMA，独立串口测试仍使用阻塞发送。 */
    hdma_usart1_tx.Instance = DMA1_Channel4;
    hdma_usart1_tx.Init.Direction = DMA_MEMORY_TO_PERIPH;
    hdma_usart1_tx.Init.PeriphInc = DMA_PINC_DISABLE;
    hdma_usart1_tx.Init.MemInc = DMA_MINC_ENABLE;
    hdma_usart1_tx.Init.PeriphDataAlignment = DMA_PDATAALIGN_BYTE;
    hdma_usart1_tx.Init.MemDataAlignment = DMA_MDATAALIGN_BYTE;
    hdma_usart1_tx.Init.Mode = DMA_NORMAL;
    hdma_usart1_tx.Init.Priority = DMA_PRIORITY_MEDIUM;
    if (HAL_DMA_Init(&hdma_usart1_tx) != HAL_OK)
    {
        Fail(10U);
        return HAL_ERROR;
    }
    __HAL_LINKDMA(&huart1, hdmatx, hdma_usart1_tx);
    HAL_NVIC_SetPriority(DMA1_Channel4_IRQn, 2U, 0U);
    HAL_NVIC_EnableIRQ(DMA1_Channel4_IRQn);
    HAL_NVIC_SetPriority(USART1_IRQn, 2U, 0U);
    HAL_NVIC_EnableIRQ(USART1_IRQn);
    initialized = 1U;
    return HAL_OK;
}

/* 清零本轮统计，校准 ADC 并依次启动 ADC DMA、TIM3 触发；须先成功调用 Heart_Init；返回启动状态。 */
HAL_StatusTypeDef Heart_Start(void)
{
    if (heart_status.running) return HAL_BUSY;
    if (!initialized) { Fail(11U); return HAL_ERROR; }
    heart_status.error = 0U;
    heart_status.source = HEART_SOURCE_REAL;
    heart_status.requested_source = HEART_SOURCE_REAL;
    heart_status.source_switches = 0U;
    heart_status.source_guard_blocks = 0U;
    simulated_origin = 0U;
    heart_status.adc_blocks = 0U;
    heart_status.adc_dropped_blocks = 0U;
    heart_status.uart_sent_blocks = 0U;
    heart_status.uart_dropped_blocks = 0U;
    heart_status.uart_status = HAL_ERROR;
    tx_head = tx_tail = tx_count = adc_consumed = 0U;
    tx_active = tx_complete = 0U;
    heart_status.lead_off = GPIO_UpdateLeadOffLEDs();
    lead_seen = heart_status.lead_off;
    if (HAL_ADCEx_Calibration_Start(&hadc1) != HAL_OK) { Fail(1U); return HAL_ERROR; }
    heart_status.running = 1U;
    if (HAL_ADC_Start_DMA(&hadc1, (uint32_t *)adc_buf, ADC_TOTAL_SAMPLES) != HAL_OK)
    {
        Fail(2U);
        (void)Heart_Stop();
        return HAL_ERROR;
    }
    __HAL_TIM_SET_COUNTER(&htim3, 0U);
    __HAL_TIM_CLEAR_FLAG(&htim3, TIM_FLAG_UPDATE);
    if (HAL_TIM_Base_Start(&htim3) != HAL_OK)
    {
        Fail(3U);
        (void)Heart_Stop();
        return HAL_ERROR;
    }
    return HAL_OK;
}

/* 停止定时触发、ADC DMA 和正在进行的串口发送，清空队列；不主动拉低 AD8232 SDN；返回清理状态。 */
HAL_StatusTypeDef Heart_Stop(void)
{
    /* 停止各外设的汇总状态；任一停止或中止操作失败则返回 HAL_ERROR。 */
    HAL_StatusTypeDef result = HAL_OK;
    if (!initialized) return HAL_OK;
    heart_status.running = 0U;
    if (HAL_TIM_Base_Stop(&htim3) != HAL_OK) result = HAL_ERROR;
    if (HAL_ADC_Stop_DMA(&hadc1) != HAL_OK) result = HAL_ERROR;
    if (tx_active && HAL_UART_AbortTransmit(&huart1) != HAL_OK) result = HAL_ERROR;
    tx_active = tx_complete = 0U;
    tx_head = tx_tail = tx_count = 0U;
    if (result != HAL_OK)
    {
        initialized = 0U; /* 清理失败后必须重新初始化。 */
        if (heart_status.error == 0U) Fail(12U);
    }
    return result;
}

/* 主循环反复调用的非阻塞处理入口：轮询导联、收集采样块、推进串口发送；有错误时停止并返回 HAL_ERROR。 */
HAL_StatusTypeDef Heart_Process(void)
{
    /* 每轮先同步指示灯；即使采集停止或报错，只要主循环继续调用就保持更新。 */
    uint32_t lead_off = GPIO_UpdateLeadOffLEDs();
    if (heart_status.error != 0U)
    {
        if (heart_status.running) (void)Heart_Stop();
        return HAL_ERROR;
    }
    if (!heart_status.running) return HAL_OK;
    heart_status.lead_off = lead_off;
    lead_seen |= heart_status.lead_off;
    ServiceUart();
    if (!heart_status.error) CollectBlock();
    if (!heart_status.error) ServiceUart();
    if (heart_status.error)
    {
        (void)Heart_Stop();
        return HAL_ERROR;
    }
    return HAL_OK;
}

/* 主循环请求切换；不在中断中调用，且不直接修改正在发送的DMA缓冲区。 */
HAL_StatusTypeDef Heart_SetSource(Heart_Source source)
{
    if (source != HEART_SOURCE_REAL && source != HEART_SOURCE_SIMULATED) return HAL_ERROR;
    if (heart_status.error != 0U || !initialized) return HAL_ERROR;
    if (!heart_status.running) return HAL_BUSY;
    heart_status.requested_source = source;
    return HAL_OK;
}

/* 下列 HAL 回调由心电模块统一实现，不要在 main.c 等文件重复定义。 */
/* ADC DMA 前半区完成回调；adc 为触发回调的 ADC 句柄；仅在 ADC1 运行时递增块计数。 */
void HAL_ADC_ConvHalfCpltCallback(ADC_HandleTypeDef *adc)
{
    if (heart_status.running && adc->Instance == ADC1) ++heart_status.adc_blocks;
}
/* ADC DMA 后半区完成回调；adc 为触发回调的 ADC 句柄；仅在 ADC1 运行时递增块计数。 */
void HAL_ADC_ConvCpltCallback(ADC_HandleTypeDef *adc)
{
    if (heart_status.running && adc->Instance == ADC1) ++heart_status.adc_blocks;
}
/* 串口发送完成回调；uart 为串口句柄；仅标记 USART1 当前块完成，队列回收由主循环执行。 */
void HAL_UART_TxCpltCallback(UART_HandleTypeDef *uart)
{
    if (heart_status.running && uart->Instance == USART1 && tx_active) tx_complete = 1U;
}
/* ADC 错误回调；adc 为 ADC 句柄；运行中的 ADC1 出错时记录错误码 4。 */
void HAL_ADC_ErrorCallback(ADC_HandleTypeDef *adc)
{
    if (heart_status.running && adc->Instance == ADC1) Fail(4U);
}
/* 串口错误回调；uart 为串口句柄；运行中的 USART1 出错时记录错误码 5。 */
void HAL_UART_ErrorCallback(UART_HandleTypeDef *uart)
{
    if (heart_status.running && uart->Instance == USART1) Fail(5U);
}
