'use strict';
// The operator's own conversational surface: Studio only, never the product (main.js constructs the api
// client this wraps with `CHAT_ROUTE` merged in only when the edition is Studio's own build, so the product
// never assembles a client capable of the call at all - see lib/api.js::CHAT_ROUTE).
//
// A null `threadId` starts a new conversation; the engine's own reply carries the `thread_id` a caller passes
// back to continue it, so this surface holds no conversation state of its own beyond what the renderer hands it.
function createChatSurface(api) {
  async function send(message, threadId) {
    if (!String(message ?? '').trim()) throw new Error('A message is required.');
    return api.chat({body: threadId ? {message, thread_id: threadId} : {message}});
  }
  return Object.freeze({send});
}
module.exports = {createChatSurface};
