#include "tim.h"



/* TIM3 的 HAL 句柄，用于产生 ADC 采样触发。 */
TIM_HandleTypeDef htim3;


/* 配置 TIM3 更新事件触发 ADC；定时器时钟为 72 MHz 时，72 MHz / 2000 / 100 = 360 Hz。 */
void MX_TIM3_Init(void)
{



  /* TIM3 计数时钟来源配置，使用内部时钟。 */
  TIM_ClockConfigTypeDef sClockSourceConfig = {0};
  /* TIM3 主模式配置，将更新事件作为 TRGO 输出。 */
  TIM_MasterConfigTypeDef sMasterConfig = {0};


  htim3.Instance = TIM3;
  htim3.Init.Prescaler = 1999;
  htim3.Init.CounterMode = TIM_COUNTERMODE_UP;
  htim3.Init.Period = 99;
  htim3.Init.ClockDivision = TIM_CLOCKDIVISION_DIV1;
  htim3.Init.AutoReloadPreload = TIM_AUTORELOAD_PRELOAD_DISABLE;
  if (HAL_TIM_Base_Init(&htim3) != HAL_OK)
  {
    Error_Handler();
  }
  sClockSourceConfig.ClockSource = TIM_CLOCKSOURCE_INTERNAL;
  if (HAL_TIM_ConfigClockSource(&htim3, &sClockSourceConfig) != HAL_OK)
  {
    Error_Handler();
  }
  sMasterConfig.MasterOutputTrigger = TIM_TRGO_UPDATE;
  sMasterConfig.MasterSlaveMode = TIM_MASTERSLAVEMODE_DISABLE;
  if (HAL_TIMEx_MasterConfigSynchronization(&htim3, &sMasterConfig) != HAL_OK)
  {
    Error_Handler();
  }


}

/* 启用 tim_baseHandle 指定的 TIM3 外设时钟。 */
void HAL_TIM_Base_MspInit(TIM_HandleTypeDef* tim_baseHandle)
{

  if(tim_baseHandle->Instance==TIM3)
  {

    __HAL_RCC_TIM3_CLK_ENABLE();

  }
}

/* 关闭 tim_baseHandle 指定的 TIM3 外设时钟。 */
void HAL_TIM_Base_MspDeInit(TIM_HandleTypeDef* tim_baseHandle)
{

  if(tim_baseHandle->Instance==TIM3)
  {

    __HAL_RCC_TIM3_CLK_DISABLE();

  }
}


