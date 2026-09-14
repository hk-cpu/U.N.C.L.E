# Builds the firmware's decode layer for this machine and runs its tests.
# The code under test is plain C99 with no ESP32 dependency, which is what
# makes checking it here possible at all.

CC ?= cc
CFLAGS = -std=c99 -Wall -Wextra -Werror -O1 -g

test_obd: test_obd.c ../cardiag-esp32/obd.c ../cardiag-esp32/obd.h
	$(CC) $(CFLAGS) -o $@ test_obd.c ../cardiag-esp32/obd.c -lm

.PHONY: all clean
all: test_obd
	./test_obd

clean:
	rm -f test_obd
