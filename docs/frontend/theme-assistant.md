# AI theme assistant

Logos has the set_ui_theme local tool in `tool_providers/theme.py`. The chat model is not offered it; it stays offered over MCP.

The tool supports the existing light, dark, dim and high-contrast schemes and is registered with requires_approval=true. It returns the chosen theme and a sanitized frontend_action with the previous theme.

Nothing enforces requires_approval, so a call runs and returns success, but the page does not read frontend_action: the tool reports success and the theme does not change. The theme changes only through the theme menu and LogosTheme.
