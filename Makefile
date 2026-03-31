CHROME = /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome
CHROME_FLAGS = --headless --default-background-color=00000000 --hide-scrollbars

preview:
	$(CHROME) $(CHROME_FLAGS) --screenshot=assets/social-preview.png \
		--window-size=1280,800 "file://$(PWD)/assets/social-preview.html"

terminal-card:
	$(CHROME) $(CHROME_FLAGS) --screenshot=assets/successful-export.png \
		--window-size=1012,800 "file://$(PWD)/assets/terminal-card.html"

assets: terminal-card preview

.PHONY: preview terminal-card assets
