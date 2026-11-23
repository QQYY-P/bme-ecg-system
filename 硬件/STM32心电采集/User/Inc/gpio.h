#ifndef __GPIO_H__
#define __GPIO_H__

#ifdef __cplusplus
extern "C" {
#endif


#include "main.h"


/* PB0/PB1 为导联检测输入，PB12/PB13 为对应指示输出；PB10 保持高电平使能 AD8232。 */
void MX_GPIO_Init(void);

/* 最近一次 LO+ 指示输出电平：0 熄灭、1 点亮；供调试观察，请勿由其他代码修改。 */
extern volatile uint8_t lo_plus_led_state;
/* 最近一次 LO- 指示输出电平：0 熄灭、1 点亮；供调试观察，请勿由其他代码修改。 */
extern volatile uint8_t lo_minus_led_state;

/* 读取 PB0/PB1 并分别同步至 PB12/PB13；任一输入为高返回 1，否则返回 0。
 * 在 MX_GPIO_Init 后由主循环反复调用；仅跟随电平，不锁存、不反相。 */
uint32_t GPIO_UpdateLeadOffLEDs(void);




/* 非阻塞消抖后的单次按下事件；主循环轮询，长按不重复，上电按住需释放后再按。 */
uint32_t GPIO_ModeKeyPressed(void);

#ifdef __cplusplus
}
#endif
#endif 

