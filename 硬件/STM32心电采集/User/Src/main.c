#include "main.h"
#include "usart.h"
#include "heart.h"
#include "gpio.h"

/* 心电应用阶段：0 未开始；1 HAL；2 系统时钟；3 心电初始化；4 启动采集；5 连续采集。 */
volatile uint32_t app_stage = 0U;
/* 应用错误码：优先记录 heart_status.error；否则为 100+阶段；200 为断言失败。 */
volatile uint32_t app_error = 0U;
/* 最近一次心电模块接口返回值，供 Keil Watch 观察。 */
volatile HAL_StatusTypeDef app_status = HAL_ERROR;

/* 使用外部 8 MHz 晶振经 PLL 九倍频，与心电模块的 72/36/72 MHz 总线时钟保持一致。 */
void SystemClock_Config(void)
{
    /* 外部晶振及 PLL 配置；保留内部 HSI 作为启动时钟。 */
    RCC_OscInitTypeDef osc = {0};
    /* SYSCLK/HCLK 为 72 MHz，APB1 为 36 MHz，APB2 为 72 MHz。 */
    RCC_ClkInitTypeDef clock = {0};
    /* ADC 使用 APB2 六分频，得到与心电模块一致的 12 MHz 时钟。 */
    RCC_PeriphCLKInitTypeDef adc_clock = {0};

    osc.OscillatorType = RCC_OSCILLATORTYPE_HSE | RCC_OSCILLATORTYPE_HSI;
    osc.HSEState = RCC_HSE_ON;
    osc.HSEPredivValue = RCC_HSE_PREDIV_DIV1;
    osc.HSIState = RCC_HSI_ON;
    osc.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    osc.PLL.PLLState = RCC_PLL_ON;
    osc.PLL.PLLSource = RCC_PLLSOURCE_HSE;
    osc.PLL.PLLMUL = RCC_PLL_MUL9;
    if (HAL_RCC_OscConfig(&osc) != HAL_OK) Error_Handler();

    clock.ClockType = RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_HCLK |
                      RCC_CLOCKTYPE_PCLK1 | RCC_CLOCKTYPE_PCLK2;
    clock.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    clock.AHBCLKDivider = RCC_SYSCLK_DIV1;
    clock.APB1CLKDivider = RCC_HCLK_DIV2;
    clock.APB2CLKDivider = RCC_HCLK_DIV1;
    /* 72 MHz 时设置 Flash 两个等待周期；HAL 同时更新系统时钟和毫秒时基。 */
    if (HAL_RCC_ClockConfig(&clock, FLASH_LATENCY_2) != HAL_OK) Error_Handler();

    adc_clock.PeriphClockSelection = RCC_PERIPHCLK_ADC;
    adc_clock.AdcClockSelection = RCC_ADCPCLK2_DIV6;
    if (HAL_RCCEx_PeriphCLKConfig(&adc_clock) != HAL_OK) Error_Handler();
}

/* 阻塞发送完成后函数才返回，因此局部消息数组在发送期间有效。 */
/* 阻塞发送一行 USART1 TX OK，超时为 100 ms；返回 HAL 发送状态，调用结束后局部发送缓冲区才失效。 */
HAL_StatusTypeDef UART_SendTest(void)
{
    /* 串口测试文本，末尾回车换行；发送时不包含字符串结束符。 */
    uint8_t message[] = "USART1 TX OK\r\n";
    return HAL_UART_Transmit(&huart1, message, sizeof(message) - 1U, 100U);
}

/* 程序入口：启动 PA0 的 360 Hz 心电采集，通过USART1 DMA发送带来源标识的七字节帧。 */
int main(void)
{
    app_stage = 1U;
    HAL_Init();
    app_stage = 2U;
    SystemClock_Config();
    app_stage = 3U;
    /* 模块统一初始化 GPIO、ADC、定时器和串口 DMA，不重复初始化外设。 */
    app_status = Heart_Init();
    if (app_status != HAL_OK) Error_Handler();
    app_stage = 4U;
    app_status = Heart_Start();
    if (app_status != HAL_OK) Error_Handler();
    app_stage = 5U;
    while (1)
    {
        /* SW2/PC13高电平按下：消抖后只切换一次；长按不重复触发。
         * 以请求状态为准，快速再次按下可以撤回尚未提交的切换请求。 */
        if (GPIO_ModeKeyPressed() != 0U)
        {
            app_status = Heart_SetSource(heart_status.requested_source == HEART_SOURCE_REAL
                                        ? HEART_SOURCE_SIMULATED : HEART_SOURCE_REAL);
            if (app_status != HAL_OK) Error_Handler();
        }
        /* 同一个非阻塞发送通道处理真实/模拟数据；不调用阻塞串口测试或HAL_Delay。 */
        app_status = Heart_Process();
        if (app_status != HAL_OK) Error_Handler();
    }
}

/* 保留心电模块错误码或初始化阶段，关闭中断并停留，供调试定位。 */
void Error_Handler(void)
{
    if (app_error == 0U)
        app_error = (heart_status.error != 0U) ? heart_status.error : 100U + app_stage;
    __disable_irq();
    while (1) {}
}

#ifdef USE_FULL_ASSERT
/* 完整断言启用时的失败入口；file 和 line 分别是源文件与行号，本实现不输出它们，只记录错误码 200 后停机。 */
void assert_failed(uint8_t *file, uint32_t line)
{
    (void)file;
    (void)line;
    app_error = 200U;
    Error_Handler();
}
#endif
