#ifndef __DMA_H__
#define __DMA_H__

#ifdef __cplusplus
extern "C" {
#endif


#include "main.h"

/* 启用 DMA1 时钟及通道 1、通道 4 中断；ADC 优先级为 1，串口发送优先级为 2。 */
void MX_DMA_Init(void);


#ifdef __cplusplus
}
#endif

#endif 

