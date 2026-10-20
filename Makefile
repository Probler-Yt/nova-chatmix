NAME     := nova-chatmix
VERSION  := $(shell sed -n 's/^__version__ = "\(.*\)"/\1/p' src/nova-chatmix.py)
PREFIX   ?= /usr
BINDIR   ?= $(PREFIX)/bin
UDEVDIR  ?= $(PREFIX)/lib/udev/rules.d
UNITDIR  ?= $(PREFIX)/lib/systemd/user
APPDIR   ?= $(PREFIX)/share/applications
DOCDIR   ?= $(PREFIX)/share/doc/$(NAME)
LICDIR   ?= $(PREFIX)/share/licenses/$(NAME)
DESTDIR  ?=

.PHONY: all install uninstall test test-core dist version

all:
	@echo "Nothing to build. Use 'make install' (packagers: DESTDIR=... PREFIX=/usr)."

install:
	install -Dm755 src/nova-chatmix.py      "$(DESTDIR)$(BINDIR)/nova-chatmix"
	install -Dm755 src/nova-chatmix-gui.py  "$(DESTDIR)$(BINDIR)/nova-chatmix-gui"
	install -Dm644 data/70-nova-chatmix.rules "$(DESTDIR)$(UDEVDIR)/70-nova-chatmix.rules"
	install -dm755 "$(DESTDIR)$(UNITDIR)" "$(DESTDIR)$(APPDIR)"
	sed 's|@BINDIR@|$(BINDIR)|g' data/nova-chatmix.service > "$(DESTDIR)$(UNITDIR)/nova-chatmix.service"
	sed 's|@BINDIR@|$(BINDIR)|g' data/nova-chatmix.desktop > "$(DESTDIR)$(APPDIR)/nova-chatmix.desktop"
	chmod 644 "$(DESTDIR)$(UNITDIR)/nova-chatmix.service" "$(DESTDIR)$(APPDIR)/nova-chatmix.desktop"
	install -Dm644 README.md "$(DESTDIR)$(DOCDIR)/README.md"
	install -Dm644 LICENSE   "$(DESTDIR)$(LICDIR)/LICENSE"

uninstall:
	rm -f "$(DESTDIR)$(BINDIR)/nova-chatmix" "$(DESTDIR)$(BINDIR)/nova-chatmix-gui" \
	      "$(DESTDIR)$(UDEVDIR)/70-nova-chatmix.rules" "$(DESTDIR)$(UNITDIR)/nova-chatmix.service" \
	      "$(DESTDIR)$(APPDIR)/nova-chatmix.desktop"
	rm -rf "$(DESTDIR)$(DOCDIR)" "$(DESTDIR)$(LICDIR)"

# Everything is simulated (fake DAC, fake PipeWire, fake /proc) - no hardware needed.
test:
	python3 tests/run.py

test-core:
	python3 tests/run.py --no-gui

dist:
	@mkdir -p dist
	git archive --format=tar.gz --prefix=$(NAME)-$(VERSION)/ -o dist/$(NAME)-$(VERSION).tar.gz v$(VERSION)
	@echo dist/$(NAME)-$(VERSION).tar.gz

version:
	@echo $(VERSION)
