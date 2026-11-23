#include "gpio.h"

/* 按下和释放均需稳定30ms，不使用HAL_Delay阻塞采样。 */
#define MODE_KEY_DEBOUNCE_MS 30U
/* 最近原始电平、已确认电平，以及释放后允许触发一次的标志。 */
static GPIO_PinState key_raw, key_stable;
static uint8_t key_armed;
/* 最近一次原始电平改变的毫秒时刻；用无符号差值处理时基回绕。 */
static uint32_t key_changed_at;

/* 最近一次采样的 LO+ 电平，同时也是 PB12 的目标输出状态。 */
volatile uint8_t lo_plus_led_state = 0U;
/* 最近一次采样的 LO- 电平，同时也是 PB13 的目标输出状态。 */
volatile uint8_t lo_minus_led_state = 0U;


/* PB0/PB1 为导联检测输入，PB12/PB13 为对应指示输出；PB10 保持高电平使能 AD8232。 */
void MX_GPIO_Init(void)
{

  /* 复用的 GPIO 配置结构，依次设置导联检测输入和 SDN 输出。 */
  GPIO_InitTypeDef GPIO_InitStruct = {0};


  __HAL_RCC_GPIOC_CLK_ENABLE();
  __HAL_RCC_GPIOD_CLK_ENABLE();
  __HAL_RCC_GPIOA_CLK_ENABLE();
  __HAL_RCC_GPIOB_CLK_ENABLE();


  /* 先预置输出寄存器为高，再切换为推挽输出，避免初始化时短暂拉低 SDN。
   * SDN 低有效：高电平使关断控制无效，AD8232 处于使能状态。 */
  HAL_GPIO_WritePin(AD8232_SDN_GPIO_Port, AD8232_SDN_Pin, GPIO_PIN_SET);


  GPIO_InitStruct.Pin = AD8232_LO__Pin|AD8232_LO_B1_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);


  /* 指示输出先预置低电平，避免切换为输出时误亮。 */
  HAL_GPIO_WritePin(AD8232_LO_PLUS_LED_GPIO_Port, AD8232_LO_PLUS_LED_Pin, GPIO_PIN_RESET);
  HAL_GPIO_WritePin(AD8232_LO_MINUS_LED_GPIO_Port, AD8232_LO_MINUS_LED_Pin, GPIO_PIN_RESET);
  GPIO_InitStruct.Pin = AD8232_LO_PLUS_LED_Pin | AD8232_LO_MINUS_LED_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(GPIOB, &GPIO_InitStruct);

  GPIO_InitStruct.Pin = AD8232_SDN_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_OUTPUT_PP;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  GPIO_InitStruct.Speed = GPIO_SPEED_FREQ_LOW;
  HAL_GPIO_Init(AD8232_SDN_GPIO_Port, &GPIO_InitStruct);

  /* SW2已有外部下拉，使用浮空输入；PA0留给ADC，NRST保留复位。 */
  GPIO_InitStruct.Pin = MODE_KEY_Pin;
  GPIO_InitStruct.Mode = GPIO_MODE_INPUT;
  GPIO_InitStruct.Pull = GPIO_NOPULL;
  HAL_GPIO_Init(MODE_KEY_GPIO_Port, &GPIO_InitStruct);
  key_raw = HAL_GPIO_ReadPin(MODE_KEY_GPIO_Port, MODE_KEY_Pin);
  key_stable = key_raw;
  key_changed_at = HAL_GetTick();
  key_armed = (key_raw == GPIO_PIN_RESET) ? 1U : 0U;

  /* 初始化结束时两路 LED 保持低电平；主循环调用更新函数后再跟随 LO 输入。 */
  lo_plus_led_state = 0U;
  lo_minus_led_state = 0U;

}



/* 将两路导联检测电平独立复制到 LED 输出，返回两路输入的逻辑或。 */
uint32_t GPIO_UpdateLeadOffLEDs(void)
{
  /* LO+ 输入快照，用于同步该通道输出及调试变量。 */
  GPIO_PinState plus_state = HAL_GPIO_ReadPin(AD8232_LO__GPIO_Port, AD8232_LO__Pin);
  /* LO- 输入快照；独立读取，避免逻辑或短路跳过第二路输入。 */
  GPIO_PinState minus_state = HAL_GPIO_ReadPin(AD8232_LO_B1_GPIO_Port, AD8232_LO_B1_Pin);

  HAL_GPIO_WritePin(AD8232_LO_PLUS_LED_GPIO_Port, AD8232_LO_PLUS_LED_Pin, plus_state);
  HAL_GPIO_WritePin(AD8232_LO_MINUS_LED_GPIO_Port, AD8232_LO_MINUS_LED_Pin, minus_state);
  lo_plus_led_state = (plus_state == GPIO_PIN_SET) ? 1U : 0U;
  lo_minus_led_state = (minus_state == GPIO_PIN_SET) ? 1U : 0U;
  return (uint32_t)(lo_plus_led_state | lo_minus_led_state);
}

/* 返回1只代表一次已消抖的新按下；主循环必须持续调用。 */
uint32_t GPIO_ModeKeyPressed(void)
{
    /* 输入快照和当前毫秒数，避免多次读取造成不一致。 */
    GPIO_PinState level = HAL_GPIO_ReadPin(MODE_KEY_GPIO_Port, MODE_KEY_Pin);
    uint32_t now = HAL_GetTick();
    if (level != key_raw)
    {
        key_raw = level;
        key_changed_at = now;
    }
    if ((uint32_t)(now - key_changed_at) < MODE_KEY_DEBOUNCE_MS || level == key_stable)
        return 0U;
    key_stable = level;
    if (level == GPIO_PIN_RESET)
    {
        key_armed = 1U;
        return 0U;
    }
    if (key_armed != 0U)
    {
        key_armed = 0U;
        return 1U;
    }
    return 0U;
}
