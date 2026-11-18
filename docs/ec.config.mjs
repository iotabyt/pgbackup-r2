// @ts-check
// Expressive Code (code blocks). Its own file because these options take functions, which
// can't go through astro.config.mjs.
import { defineEcConfig } from "@astrojs/starlight/expressive-code";

// GitHub's themes colour keywords and operators red. Red is the brand's spark, kept for links
// and the logo, so those tokens use the themes' own blue instead.
/** @type {Record<string, string>} */
const NOT_RED = { "#cf222e": "#0550ae", "#ff7b72": "#79c0ff" };

export default defineEcConfig({
  themes: ["github-dark-default", "github-light-default"],
  customizeTheme: (theme) => {
    for (const rule of theme.settings) {
      const fg = rule.settings?.foreground?.toLowerCase();
      if (fg && NOT_RED[fg]) rule.settings.foreground = NOT_RED[fg];
    }
    return theme;
  },
  styleOverrides: {
    codeFontFamily: "'JetBrains Mono', ui-monospace, monospace",
    uiFontFamily: "'Outfit Variable', system-ui, sans-serif",
    borderRadius: "0.375rem",
    borderColor: ({ theme }) => (theme.type === "dark" ? "#1C2742" : "#E3E5EA"),
    codeBackground: ({ theme }) => (theme.type === "dark" ? "#0B1530" : "#F5F6F8"),
    frames: {
      editorTabBarBackground: ({ theme }) => (theme.type === "dark" ? "#00091E" : "#FFFFFF"),
      terminalTitlebarBackground: ({ theme }) => (theme.type === "dark" ? "#00091E" : "#FFFFFF"),
      terminalBackground: ({ theme }) => (theme.type === "dark" ? "#0B1530" : "#F5F6F8"),
      frameBoxShadowCssValue: "none",
      // The active file tab's top line would be the theme's orange-red: keep it navy.
      editorActiveTabIndicatorTopColor: ({ theme }) =>
        theme.type === "dark" ? "#FFFFFF" : "#00091E",
      editorActiveTabIndicatorBottomColor: "transparent",
    },
  },
});
