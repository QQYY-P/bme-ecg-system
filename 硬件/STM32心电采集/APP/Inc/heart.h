#ifndef HEART_H
#define HEART_H

#include "stm32f1xx_hal.h"

#ifdef __cplusplus
extern "C" {
#endif

/* 心电设计采样率，单位 Hz；这里只声明约定，实际频率由系统时钟与 TIM3 参数决定。 */
#define HEART_SAMPLE_RATE_HZ 360U
/* 每块采样点数；在 360 Hz 下对应约 100 ms 数据。 */
#define HEART_BLOCK_SAMPLES 36U

/* 串口数据来源：模拟模式仅为功能演示，不代表真实心电或病理标签。 */
typedef enum
{
    HEART_SOURCE_REAL = 0,
    HEART_SOURCE_SIMULATED = 1
} Heart_Source;

/* 心电模块诊断状态，各累计计数在 Heart_Start 时清零。 */
typedef struct
{
    /* 运行标志：1 表示采集已进入运行流程，0 表示停止。 */
    uint32_t running;
    /* 已应用到新发送块的数据来源；0真实、1模拟。 */
    Heart_Source source;
    /* 主循环请求的来源，在下一可入队完整块边界应用。 */
    Heart_Source requested_source;
    /* 实际完成的来源切换次数，以及发送的隔离无效块数。 */
    uint32_t source_switches;
    uint32_t source_guard_blocks;
    /* 最近记录的错误码；0 表示无错误，具体含义见下方错误码说明。 */
    uint32_t error;
    /* ADC 半缓冲或全缓冲回调累计完成的块数。 */
    uint32_t adc_blocks;
    /* 主循环未及时取走、已被更新的 ADC 块累计数量。 */
    uint32_t adc_dropped_blocks;
    /* 串口完成回调被主循环确认并回收的发送块数。 */
    uint32_t uart_sent_blocks;
    /* 因发送队列满而丢弃的新采样块数。 */
    uint32_t uart_dropped_blocks;
    /* 最近一次轮询的导联状态；任一检测脚为高即记为 1。 */
    uint32_t lead_off;
    /* 最近一次 HAL_UART_Transmit_DMA 调用的返回状态，不等同于最终物理发送完成状态。 */
    HAL_StatusTypeDef uart_status;
} Heart_Status;

/* 对外提供只读诊断信息，可在应用代码或 Keil Watch 中观察。 */
extern volatile Heart_Status heart_status;

/*
 * 心电模块调用约定：在 HAL_Init 及系统时钟配置完成后，从主循环上下文调用。
 * 所需时钟：HCLK=72 MHz、PCLK1=36 MHz、PCLK2=72 MHz（通常为 8 MHz HSE 经 PLL 九倍频）。
 * 时钟不符时 Heart_Init 返回 HAL_ERROR，并记录 error=8。
 * 初始化内容：GPIO、ADC1 循环 DMA、TIM3、USART1 发送 DMA，以及 ADC 时钟六分频。
 * 模块占用 ADC1、TIM3、USART1、DMA1 通道 1/4 及相应 HAL 回调；不要重复定义回调。
 * 运行期间不要通过同一串口调用 printf 或其他发送函数。
 * Heart_Process 必须持续执行，主循环中不要加入 HAL_Delay(1000) 等长时间阻塞。
 *
 * if (Heart_Init() != HAL_OK) Error_Handler();
 * if (Heart_Start() != HAL_OK) Error_Handler();
 * while (1) {
 *     if (Heart_Process() != HAL_OK) Error_Handler();
 * }
 *
 * 输出协议：每 100 ms 形成 36个七字节二进制帧（共252字节），串口为 115200、8N1，PA9 接上位机 RX。
 * 每点依次为：AA 55、模式（0真实/1模拟）、8 bit序号、数据高字节、数据低字节、校验。
 * 校验为模式、序号、数据高低字节之和的低8位；不包含帧头，不追加换行。
 * 序号按采样位置从 0 开始，255 后回到 0；Heart_Start 会重置采样计数。
 * 0～4095 为真实ADC值或模拟心电值；0xFFFF 表示导联脱落或模式切换隔离点。
 * PC13/SW2切换来源；每次切换插入一块36点无效数据，不中止UART DMA，不重置序号。
 * 模拟波形由300点常量表生成，360 Hz下约72次/分；模拟模式忽略导联脱落对数据的屏蔽。
 * ADC/TIM3在两种模式均运行，提供相同节拍；LED仍反映真实LO输入。每帧携带本块已生效的来源，隔离块携带新模式，排队旧块保留旧模式。
 * LO+/LO- 由主循环轮询，与每个 ADC 点不同步，不能用它准确定位单点脱落时刻。
 * 队列满时丢弃新块并计数，后续序号保留采样位置跳变；不能仅用接收索引/360 计算时间。
 * 8 bit 序号只能反映模 256 的缺口，无法识别全部丢失情况；累加校验不能检出所有多字节错误。
 * 上位机须按新七字节协议查找帧头并验证模式、数值和校验；旧三字节解析器不兼容。
 * 模式变化或无效点应重置滤波及分类上下文；0xFFFF不作为有效ADC值。
 * 错误码：1 ADC 校准；2 ADC 启动；3 定时器启动；4 ADC 错误回调；5 串口错误回调；
 * 6 串口 DMA 启动；7 发送超时；8 系统时钟不符；9 ADC 时钟配置；10 串口 DMA 配置；
 * 11 尚未初始化；12 停止外设失败。其他 MX 初始化失败进入工程的 Error_Handler。
 */
/* 初始化心电采集外设及 USART1 发送 DMA；要求 HCLK/PCLK1/PCLK2 为 72/36/72 MHz；返回 HAL_OK、HAL_BUSY 或 HAL_ERROR。 */
HAL_StatusTypeDef Heart_Init(void);
/* 清零本轮统计，校准 ADC 并依次启动 ADC DMA、TIM3 触发；须先成功调用 Heart_Init；返回启动状态。 */
HAL_StatusTypeDef Heart_Start(void);
/* 主循环反复调用的非阻塞处理入口：轮询导联、收集采样块、推进串口发送；有错误时停止并返回 HAL_ERROR。 */
HAL_StatusTypeDef Heart_Process(void);
/* 运行中请求切换来源；不停止DMA，在后续完整块边界应用；无效参数返回HAL_ERROR。 */
HAL_StatusTypeDef Heart_SetSource(Heart_Source source);
/* 停止定时触发、ADC DMA 和正在进行的串口发送，清空队列；不主动拉低 AD8232 SDN；返回清理状态。 */
HAL_StatusTypeDef Heart_Stop(void);

#ifdef __cplusplus
}
#endif
#endif
