#ifndef __USART_H__
#define __USART_H__

#ifdef __cplusplus
extern "C" {
#endif


#include "main.h"

/* STM32F103 的 12 位 ADC 最大原始码值；用于软件串口测试的数据范围。 */
#define UART_TEST_ADC_MAX_VALUE 4095U

/* 七字节帧：AA 55、模式、序号、数据高字节、数据低字节、累加校验。 */
#define UART_SAMPLE_FRAME_BYTES 7U
/* 固定帧头，用于接收端搜索帧边界。 */
#define UART_SAMPLE_HEADER_0 0xAAU
#define UART_SAMPLE_HEADER_1 0x55U
/* 数据来源：真实ADC采集为0，软件模拟（含独立递增测试）为1。 */
#define UART_SAMPLE_MODE_REAL 0U
#define UART_SAMPLE_MODE_SIMULATED 1U
/* 导联脱落等无效采样的线上标记，位于正常 12 位 ADC 范围之外。 */
#define UART_SAMPLE_INVALID_VALUE 0xFFFFU



/* USART1 句柄，由 usart.c 定义，供测试程序与心电模块使用。 */
extern UART_HandleTypeDef huart1;


/* 配置 USART1 为 115200、8 数据位、1 停止位、无校验、仅发送模式；DMA 由心电模块另行启用。 */
void MX_USART1_UART_Init(void);

/* 将模式、序号和值编码为七字节帧；out 至少7字节，mode只能为上述0/1。返回7。 */
uint16_t UART_EncodeSampleFrame(uint8_t *out, uint8_t mode, uint8_t sequence, uint16_t value);

/* 串口递增测试完整单步：发送模式为1的七字节帧，成功后推进计数并延时 10 ms。
 * 序号按 0～255、测试值按 0～4095 各自循环；失败时不推进，返回 HAL 错误状态。
 * 先完成 HAL、系统时钟和 USART1 初始化，再在主循环反复调用；不要重复加测试延时。
 * 独占 USART1，不与心电 DMA/printf 同时发送；不初始化或修改 GPIO，不读取 ADC。
 * 调用示例：if (UART_SendRampTest() != HAL_OK) Error_Handler(); */
HAL_StatusTypeDef UART_SendRampTest(void);



#ifdef __cplusplus
}
#endif

#endif 

