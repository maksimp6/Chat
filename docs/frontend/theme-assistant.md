# AI theme assistant

Alice Pro exposes the set_ui_theme local tool to the model.

The tool supports the existing light, dark, dim and high-contrast schemes. It is a mutating UI operation and therefore is registered with requires_approval=true; the existing approval card is the consent gate.

After approval, the server returns a sanitized frontend_action. The browser applies the requested theme through AliceTheme, without exposing secrets or internal state. The previous theme is carried only as rollback metadata, and the UI offers a one-click restore action.

The assistant does not bypass the user's choice: selecting a theme is proposed by the model, then explicitly approved by the user before the client applies it.

Theme accessibility regression tests verify WCAG AA contrast for the primary application, user-message and assistant-message text pairs.
