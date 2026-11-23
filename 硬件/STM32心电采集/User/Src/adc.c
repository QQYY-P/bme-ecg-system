#include "adc.h"


/* ADC1 的 HAL 句柄，保存采样配置和状态。 */
ADC_HandleTypeDef hadc1;
/* ADC1 关联的 DMA1 通道 1 句柄。 */
DMA_HandleTypeDef hdma_adc1;


/* 配置 ADC1 单通道采集 PA0，使用 TIM3 TRGO 外部触发、右对齐和 239.5 周期采样时间。 */
void MX_ADC1_Init(void)
{


  /* ADC 规则通道配置，选择 PA0 对应的通道 0。 */
  ADC_ChannelConfTypeDef sConfig = {0};


  hadc1.Instance = ADC1;
  hadc1.Init.ScanConvMode = ADC_SCAN_DISABLE;
  hadc1.Init.ContinuousConvMode = DISABLE;
  hadc1.Init.DiscontinuousConvMode = DISABLE;
  hadc1.Init.ExternalTrigConv = ADC_EXTERNALTRIGCONV_T3_TRGO;
  hadc1.Init.DataAlign = ADC_DATAALIGN_RIGHT;
  hadc1.Init.NbrOfConversion = 1;
  if (HAL_ADC_Init(&hadc1) != HAL_OK)
  {
    Error_Handler();
  }

  sConfig.Channel = ADC_CHANNEL_0;
  sConfig.Rank = ADC_REGULAR_RANK_1;
  sConfig.SamplingTime = ADC_SAMPLETIME_239CYCLES_5;
  if (HAL_ADC_ConfigChannel(&hadc1, &sConfig) != HAL_OK)
  {
    Error_Handler();
  }

}

/* 为 adcHandle 对应的 ADC1 启用时钟，配置 PA0 模拟输入和 DMA1 通道 1 的半字循环搬运。 */
void HAL_ADC_MspInit(ADC_HandleTypeDef* adcHandle)
{

  /* ADC 输入引脚 PA0 的模拟模式配置。 */
  GPIO_InitTypeDef GPIO_InitStruct = {0};
  if(adcHandle->Instance==ADC1)
  {

    __HAL_RCC_ADC1_CLK_ENABLE();

    __HAL_RCC_GPIOA_CLK_ENABLE();

    GPIO_InitStruct.Pin = GPIO_PIN_0;
    GPIO_InitStruct.Mode = GPIO_MODE_ANALOG;
    HAL_GPIO_Init(GPIOA, &GPIO_InitStruct);


    hdma_adc1.Instance = DMA1_Channel1;
    hdma_adc1.Init.Direction = DMA_PERIPH_TO_MEMORY;
    hdma_adc1.Init.PeriphInc = DMA_PINC_DISABLE;
    hdma_adc1.Init.MemInc = DMA_MINC_ENABLE;
    hdma_adc1.Init.PeriphDataAlignment = DMA_PDATAALIGN_HALFWORD;
    hdma_adc1.Init.MemDataAlignment = DMA_MDATAALIGN_HALFWORD;
    hdma_adc1.Init.Mode = DMA_CIRCULAR;
    hdma_adc1.Init.Priority = DMA_PRIORITY_HIGH;
    if (HAL_DMA_Init(&hdma_adc1) != HAL_OK)
    {
      Error_Handler();
    }

    __HAL_LINKDMA(adcHandle,DMA_Handle,hdma_adc1);

  }
}

/* 释放 adcHandle 对应 ADC1 的时钟、PA0 引脚与关联 DMA。 */
void HAL_ADC_MspDeInit(ADC_HandleTypeDef* adcHandle)
{

  if(adcHandle->Instance==ADC1)
  {

    __HAL_RCC_ADC1_CLK_DISABLE();


    HAL_GPIO_DeInit(GPIOA, GPIO_PIN_0);


    HAL_DMA_DeInit(adcHandle->DMA_Handle);

  }
}



