
#define COUT1       ((void (*)(char)) 0xFDF0)
#define HOME        ((void (*)(void)) 0xFC58)
#define MOTOR_OFF   (*(volatile char *) 0xC0E8)

#define TOP_BIT_BYTE (0x80)

char* MESSAGE = "\rHELLO, WORLD!\r";

void start(void) {
    char i;
    volatile char x = MOTOR_OFF;
    HOME();

    i = 0;
    while(MESSAGE[i]) {
        COUT1(MESSAGE[i] | TOP_BIT_BYTE);
        i++;
    }

    for(;;);
}
