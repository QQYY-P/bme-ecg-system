#include "usart.h"

/* 串口测试每帧成功发送后的延时，单位 ms；不代表精确的采样周期。 */
#define UART_RAMP_DELAY_MS 10U

/* USART1 的 HAL 句柄，保存配置和发送状态。 */
UART_HandleTypeDef huart1;
/* 保留句柄供中断文件引用；单独调用串口初始化不会启用发送 DMA。 */
/* USART1 发送 DMA 句柄；独立串口测试不启用，由心电模块初始化时配置。 */
DMA_HandleTypeDef hdma_usart1_tx;

/* 配置 USART1 为 115200、8 数据位、1 停止位、无校验、仅发送模式；DMA 由心电模块另行启用。 */
void MX_USART1_UART_Init(void)
{
    huart1.Instance = USART1;
    huart1.Init.BaudRate = 115200;
    huart1.Init.WordLength = UART_WORDLENGTH_8B;
    huart1.Init.StopBits = UART_STOPBITS_1;
    huart1.Init.Parity = UART_PARITY_NONE;
    huart1.Init.Mode = UART_MODE_TX;
    huart1.Init.HwFlowCtl = UART_HWCONTROL_NONE;
    huart1.Init.OverSampling = UART_OVERSAMPLING_16;
    if (HAL_UART_Init(&huart1) != HAL_OK) Error_Handler();
}

/* 初始化 uart 对应串口的底层资源；仅处理 USART1，启用时钟、配置 PA9 复用推挽，并关闭独立测试不用的中断。 */
void HAL_UART_MspInit(UART_HandleTypeDef *uart)
{
    /* USART1 TX 引脚 PA9 的 GPIO 配置结构。 */
    GPIO_InitTypeDef pin = {0};
    if (uart->Instance != USART1) return;
    __HAL_RCC_AFIO_CLK_ENABLE();
    __HAL_RCC_GPIOA_CLK_ENABLE();
    __HAL_RCC_USART1_CLK_ENABLE();
    __HAL_AFIO_REMAP_USART1_DISABLE();
    pin.Pin = GPIO_PIN_9;
    pin.Mode = GPIO_MODE_AF_PP;
    pin.Pull = GPIO_NOPULL;
    pin.Speed = GPIO_SPEED_FREQ_HIGH;
    HAL_GPIO_Init(GPIOA, &pin);
    /* 阻塞发送测试不使用 DMA 和 USART 中断。 */
    HAL_NVIC_DisableIRQ(USART1_IRQn);
    HAL_NVIC_DisableIRQ(DMA1_Channel4_IRQn);
}

/* 释放 uart 对应的 USART1 时钟与 PA9 引脚，并关闭 USART1 中断。 */
void HAL_UART_MspDeInit(UART_HandleTypeDef *uart)
{
    if (uart->Instance != USART1) return;
    __HAL_RCC_USART1_CLK_DISABLE();
    HAL_GPIO_DeInit(GPIOA, GPIO_PIN_9);
    HAL_NVIC_DisableIRQ(USART1_IRQn);
}

/*
 * 编码一个采样点，返回固定长度7字节；out必须指向至少7字节可写缓冲区。
 * mode为0真实、1模拟；sequence 是 8 bit 样本序号；value 为 0～4095，0xFFFF 专用于无效采样标记。
 * 线上顺序为AA 55、模式、序号、数据高字节、数据低字节、校验。
 * 校验为模式至数据低字节的累加和低8位，不包含帧头；不附加文本或换行。
 * 本函数只写缓冲区，不发送串口，不保存或推进序号。
 */
uint16_t UART_EncodeSampleFrame(uint8_t *out, uint8_t mode, uint8_t sequence, uint16_t value)
{
    out[0] = UART_SAMPLE_HEADER_0;
    out[1] = UART_SAMPLE_HEADER_1;
    out[2] = mode;
    out[3] = sequence;
    out[4] = (uint8_t)(value >> 8);
    out[5] = (uint8_t)(value & 0xFFU);
    out[6] = (uint8_t)(out[2] + out[3] + out[4] + out[5]);
    return UART_SAMPLE_FRAME_BYTES;
}

/*
 * 串口递增测试：每次发送一个与心电采集相同格式的 7 字节二进制帧（模式为1，表示软件生成）。
 * 测试值按 0～4095 循环，序号独立按 0～255 循环，复位后的第一帧为 AA 55 01 00 00 00 01。
 * 软件生成测试值，不启动 ADC，也不受导联脱落状态影响。
 * 仅供主循环独占 USART1 时调用；发送成功后才推进序号和值。
 * 成功后内部延时 10 ms；调用者无需再加 HAL_Delay，可在主循环直接反复调用。
 * 阻塞发送超时为 100 ms；返回 HAL 状态，失败时不推进、不延时，由调用者处理错误。
 * 前提：已完成 HAL_Init、系统时钟、MX_USART1_UART_Init；不可与心电 DMA 同时占用 USART1。
 */
HAL_StatusTypeDef UART_SendRampTest(void)
{
    /* 下一个软件测试值；发送 4095 后回到 0。 */
    static uint16_t next_value = 0U;
    /* 下一个 8 bit 序号；发送 255 后回到 0，与测试值分别计数。 */
    static uint8_t next_sequence = 0U;
    /* 本次发送帧；阻塞发送返回前该局部缓冲区一直有效。 */
    uint8_t message[UART_SAMPLE_FRAME_BYTES];
    /* 记录本次发送的 HAL 返回状态。 */
    HAL_StatusTypeDef status;

    (void)UART_EncodeSampleFrame(message, UART_SAMPLE_MODE_SIMULATED, next_sequence, next_value);
    status = HAL_UART_Transmit(&huart1, message, (uint16_t)sizeof(message), 100U);
    if (status == HAL_OK)
    {
        next_sequence = (uint8_t)(next_sequence + 1U);
        next_value = (next_value >= UART_TEST_ADC_MAX_VALUE)
                   ? 0U : (uint16_t)(next_value + 1U);
        /* 测试节拍统一由本函数管理，主循环不再重复延时。 */
        HAL_Delay(UART_RAMP_DELAY_MS);
    }
    return status;
}
