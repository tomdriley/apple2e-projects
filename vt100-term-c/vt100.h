#ifndef VT100_H
#define VT100_H

/* VT100/ANSI escape-sequence parser. Feed one received byte at a time; the
 * parser renders printable text, acts on the common cursor/erase sequences via
 * the screen interface, and answers cursor-position reports over serial. */

void vt100_init(void);
void vt100_feed(char c);

/* Nonzero when the host has enabled application cursor keys (DECCKM, ESC[?1h):
 * the terminal then sends arrow keys as ESC O A..D instead of ESC [ A..D. */
unsigned char vt100_app_cursor(void);

#endif /* VT100_H */
