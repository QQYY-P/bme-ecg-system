#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif


#include "stm32f1xx_hal.h"


/* 记录心电模块错误码或初始化阶段，关闭中断并停留，供 Keil 调试器观察。 */
void Error_Handler(void);


/* AD8232 LO+ 导联检测输入 PB0；接线按工程 CubeMX 参数文档。 */
#define AD8232_LO__Pin GPIO_PIN_0
/* 第一根导联检测信号所属 GPIO 端口。 */
#define AD8232_LO__GPIO_Port GPIOB
/* AD8232 LO- 导联检测输入 PB1；任一检测信号为高时视为脱落。 */
#define AD8232_LO_B1_Pin GPIO_PIN_1
/* 第二根导联检测信号所属 GPIO 端口。 */
#define AD8232_LO_B1_GPIO_Port GPIOB
/* AD8232 关断控制引脚 PB10：本工程初始化时置高使能。 */
#define AD8232_SDN_Pin GPIO_PIN_10
/* AD8232 关断控制信号所属 GPIO 端口。 */
#define AD8232_SDN_GPIO_Port GPIOB



/* LO+ 状态指示输出 PB12：高电平点亮外接 LED。 */
#define AD8232_LO_PLUS_LED_Pin GPIO_PIN_12
/* LO+ 指示灯所属 GPIO 端口。 */
#define AD8232_LO_PLUS_LED_GPIO_Port GPIOB
/* LO- 状态指示输出 PB13：高电平点亮外接 LED。 */
#define AD8232_LO_MINUS_LED_Pin GPIO_PIN_13
/* LO- 指示灯所属 GPIO 端口。 */
#define AD8232_LO_MINUS_LED_GPIO_Port GPIOB

/* 最小系统SW2：PC13，外部4.7k下拉，按下接3.3V，高电平有效。 */
#define MODE_KEY_Pin GPIO_PIN_13
#define MODE_KEY_GPIO_Port GPIOC

#endif /* __MAIN_H */
