// The pieces of a chat surface that can be handed a file. `ChatSurface` is the whole panel — transcript, tray and
// input, with the drop target over all three; `ChatComposer` is the input row alone for a page that keeps its own
// transcript. Everything else is here for a surface that wants to wire the tray by hand.
//
// The policy those pieces enforce — the size limit, what counts as text, and the reason attached to every
// refusal — lives in `@/lib/attachments`, which holds no React and no browser of its own beyond `File`.

export { ChatSurface } from "./ChatSurface";
export { ChatComposer } from "./ChatComposer";
export { ChatDropTarget } from "./ChatDropTarget";
export { AttachmentTray } from "./AttachmentTray";
export { ComposerInput } from "./ComposerInput";
export { useAttachments, type AttachmentsApi } from "./useAttachments";
export { useComposer, type ComposerApi, type ComposerSubmit } from "./useComposer";
