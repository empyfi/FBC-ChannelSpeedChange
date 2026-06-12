PACKAGE  := enigma2-plugin-extensions-fbc-channelspeedchange
VERSION  := 0.4.4
ARCH     := all
IPK      := $(PACKAGE)_$(VERSION)_$(ARCH).ipk

SRC_DIR     := src
CONTROL_DIR := CONTROL
BUILD_DIR   := build

.PHONY: all clean ipk

all: ipk

ipk:
	@rm -rf $(BUILD_DIR)
	@mkdir -p $(BUILD_DIR)/data $(BUILD_DIR)/control
	cp -r $(SRC_DIR)/usr $(BUILD_DIR)/data/
	find $(BUILD_DIR)/data -name __pycache__ -type d -prune -exec rm -rf {} + 2>/dev/null || true
	find $(BUILD_DIR)/data -name '*.py[co]' -delete 2>/dev/null || true
	cp $(CONTROL_DIR)/* $(BUILD_DIR)/control/
	chmod 755 $(BUILD_DIR)/control/postrm 2>/dev/null || true
	echo "2.0" > $(BUILD_DIR)/debian-binary
	cd $(BUILD_DIR)/data    && tar --owner=0 --group=0 -czf ../data.tar.gz .
	cd $(BUILD_DIR)/control && tar --owner=0 --group=0 -czf ../control.tar.gz .
	cd $(BUILD_DIR) && ar -r ../$(IPK) debian-binary control.tar.gz data.tar.gz
	@echo "Built $(IPK)"

clean:
	rm -rf $(BUILD_DIR) *.ipk
