#ifndef MONITOR_H
#define MONITOR_H

/* Apple IIe hardware addresses. Definitions live in monitor.s; this header is
 * the C view of them. ROM routines are functions (compile to jsr); soft
 * switches / registers are extern volatile bytes (compile to lda/sta). */

/* --- Monitor ROM routines ------------------------------------------------ */
void HOME(void);    /* $FC58  clear 40-col screen + home cursor          */
void COUT(char c);  /* $FDED  output char in A via the CSW hook           */
void COUT1(char c); /* $FDF0  output char in A straight to the 40-col page */

/* --- Keyboard ------------------------------------------------------------ */
extern volatile unsigned char KBD;     /* $C000  bit7 = key ready, bits6-0 ASCII */
extern volatile unsigned char KBDSTRB; /* $C010  any access clears the strobe    */

/* --- 80-column / video soft switches (write to trigger) ------------------ */
extern volatile unsigned char SET80STORE; /* $C001  80STORE on (PAGE2 banks text) */
extern volatile unsigned char SET80VID;   /* $C00D  80-column video on             */
extern volatile unsigned char SETALTCHAR; /* $C00F  alternate char set (lowercase) */
extern volatile unsigned char TXTSET;     /* $C051  text mode                      */
extern volatile unsigned char MIXCLR;     /* $C052  full screen (no mixed)         */
extern volatile unsigned char TXTPAGE1;   /* $C054  PAGE2 off -> main bank         */
extern volatile unsigned char TXTPAGE2;   /* $C055  PAGE2 on  -> aux bank          */

/* --- Misc ---------------------------------------------------------------- */
extern volatile unsigned char MOTOR_OFF; /* $C0E8  drive motor off (slot 6) */
extern volatile unsigned char SPKR;      /* $C030  toggle speaker (read = click) */

/* --- Super Serial Card 6551 ACIA in slot 2 ($C0A8-$C0AB) ----------------- */
extern volatile unsigned char ACIA_DATA;    /* $C0A8  read RX / write TX byte  */
extern volatile unsigned char ACIA_STATUS;  /* $C0A9  read status / write reset */
extern volatile unsigned char ACIA_COMMAND; /* $C0AA  parity / IRQ / DTR / RTS  */
extern volatile unsigned char ACIA_CONTROL; /* $C0AB  baud / word length / stop */

#endif /* MONITOR_H */
