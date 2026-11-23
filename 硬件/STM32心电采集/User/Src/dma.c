#include "dma.h"


/* 启用 DMA1 时钟及通道 1、通道 4 中断；ADC 优先级为 1，串口发送优先级为 2。 */
void MX_DMA_Init(void)
{

  __HAL_RCC_DMA1_CLK_ENABLE();

  HAL_NVIC_SetPriority(DMA1_Channel1_IRQn, 1, 0);
  HAL_NVIC_EnableIRQ(DMA1_Channel1_IRQn);

  HAL_NVIC_SetPriority(DMA1_Channel4_IRQn, 2, 0);
  HAL_NVIC_EnableIRQ(DMA1_Channel4_IRQn);

}

