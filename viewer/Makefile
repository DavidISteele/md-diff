CC ?= cc
PKGS = gtk4 webkitgtk-6.0
CFLAGS ?= -O2 -Wall
LDLIBS = $(shell pkg-config --libs $(PKGS))

# The browser-side scripts stay JavaScript in .js files, and become C string
# literals here, so the binary carries no runtime dependency on files sitting
# next to it.
SCRIPTS = nav_js.h find_js.h

md-diff-view: md-diff-view.c $(SCRIPTS)
	$(CC) $(CFLAGS) $(shell pkg-config --cflags $(PKGS)) -o $@ $< $(LDLIBS)

%_js.h: %.js
	{ printf 'static const char %s_JS[] =\n' \
	    "$$(printf '%s' '$*' | tr 'a-z-' 'A-Z_')"; \
	  sed -e 's/\\/\\\\/g' -e 's/"/\\"/g' -e 's/^/"/' -e 's/$$/\\n"/' $<; \
	  printf ';\n'; } > $@

clean:
	rm -f md-diff-view $(SCRIPTS)

.PHONY: clean
