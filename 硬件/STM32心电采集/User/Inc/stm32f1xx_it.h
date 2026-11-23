/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file    stm32f1xx_it.h
  * @brief   This file contains the headers of the interrupt handlers.
  ******************************************************************************
  * @attention
  *
  * Copyright (c) 2026 STMicroelectronics.
  * All rights reserved.
  *
  * This software is licensed under terms that can be found in the LICENSE file
  * in the root directory of this software component.
  * If no LICENSE file comes with this software, it is provided AS-IS.
  *
  ******************************************************************************
  */
/* USER CODE END Header */

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __STM32F1xx_IT_H
#define __STM32F1xx_IT_H

#ifdef __cplusplus
extern "C" {
#endif

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
/* 不可屏蔽中断入口；当前停留在死循环中，等待调试。 */
void NMI_Handler(void);
/* 硬故障入口；当前停留在死循环中，等待调试。 */
void HardFault_Handler(void);
/* 存储管理故障入口；当前停留在死循环中，等待调试。 */
void MemManage_Handler(void);
/* 总线访问故障入口；当前停留在死循环中，等待调试。 */
void BusFault_Handler(void);
/* 非法指令或非法状态故障入口；当前停留在死循环中，等待调试。 */
void UsageFault_Handler(void);
/* 系统服务调用入口；当前没有自定义处理逻辑。 */
void SVC_Handler(void);
/* 调试监视器入口；当前没有自定义处理逻辑。 */
void DebugMon_Handler(void);
/* 可挂起系统服务入口；当前没有自定义处理逻辑。 */
void PendSV_Handler(void);
/* 系统滴答入口；更新 HAL 毫秒计数，为延时和超时检测提供时间基准。 */
void SysTick_Handler(void);
/* ADC1 DMA 中断入口；转交 HAL，触发半缓冲、全缓冲完成或错误回调。 */
void DMA1_Channel1_IRQHandler(void);
/* USART1 发送 DMA 中断入口；转交 HAL 处理 DMA 完成或错误。 */
void DMA1_Channel4_IRQHandler(void);
/* USART1 中断入口；转交 HAL 处理串口事件，包括 DMA 搬运结束后的实际发送完成。 */
void USART1_IRQHandler(void);
/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

#ifdef __cplusplus
}
#endif

#endif /* __STM32F1xx_IT_H */
