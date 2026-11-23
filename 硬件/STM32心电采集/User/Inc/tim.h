#ifndef __TIM_H__
#define __TIM_H__

#ifdef __cplusplus
extern "C" {
#endif


#include "main.h"


/* TIM3 句柄，由 tim.c 定义，供心电模块控制采样触发。 */
extern TIM_HandleTypeDef htim3;

/* 配置 TIM3 更新事件触发 ADC；定时器时钟为 72 MHz 时，72 MHz / 2000 / 100 = 360 Hz。 */
void MX_TIM3_Init(void);


#ifdef __cplusplus
}
#endif

#endif 

