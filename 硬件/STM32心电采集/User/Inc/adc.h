#ifndef __ADC_H__
#define __ADC_H__

#ifdef __cplusplus
extern "C" {
#endif


#include "main.h"

/* ADC1 句柄，由 adc.c 定义，供心电模块调用。 */
extern ADC_HandleTypeDef hadc1;

/* 配置 ADC1 单通道采集 PA0，使用 TIM3 TRGO 外部触发、右对齐和 239.5 周期采样时间。 */
void MX_ADC1_Init(void);



#endif 
