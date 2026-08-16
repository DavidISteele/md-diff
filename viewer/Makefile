CC ?= cc
PKGS = gtk4 webkitgtk-6.0
CFLAGS ?= -O2 -Wall
LDLIBS = $(shell pkg-config --libs $(PKGS))

md-diff-view: md-diff-view.c nav_js.h
	$(CC) $(CFLAGS) $(shell pkg-config --cflags $(PKGS)) -o $@ $< $(LDLIBS)

# The navigation script stays JavaScript in a .js file, and becomes a C
# string literal here, so the binary carries no runtime dependency on a
# file sitting next to it.
nav_js.h: nav.js
	{ printf 'static const char NAV_JS[] =\n'; \
	  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/^/"/' -e 's/$$/\\n"/' $<; \
	  printf ';\n'; } > $@

clean:
	rm -f md-diff-view nav_js.h

.PHONY: clean
