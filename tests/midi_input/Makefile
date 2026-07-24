# Host harness for the Tulip MIDI input path (parser core + last_midi ring).
# Compiles the REAL firmware code: ../../amy/src/amy_midi_parse.h and
# ../../tulip/shared/midi_in_ring.h. See harness.c / stress.c headers for
# what is covered and what is not.
#
#   make            build + run both (any native gcc/clang; on Windows,
#                   w64devkit: make CC=/path/to/w64devkit/bin/gcc)
#   make tsan       stress under ThreadSanitizer -- POSIX HOSTS ONLY
#                   (Linux/macOS; no Windows toolchain has a TSan runtime).
#                   Run this whenever a POSIX machine touches this code.

CC ?= gcc
CFLAGS = -O2 -g -Wall -Wextra -I../../amy/src -I../../tulip/shared

all: run

harness: harness.c ../../amy/src/amy_midi_parse.h ../../tulip/shared/midi_in_ring.h
	$(CC) $(CFLAGS) -o $@ harness.c

stress: stress.c ../../amy/src/amy_midi_parse.h ../../tulip/shared/midi_in_ring.h
	$(CC) $(CFLAGS) -pthread -o $@ stress.c

run: harness stress
	./harness
	./stress

tsan: stress.c
	$(CC) $(CFLAGS) -pthread -fsanitize=thread -o stress_tsan stress.c
	./stress_tsan

clean:
	rm -f harness stress stress_tsan *.exe

.PHONY: all run tsan clean
