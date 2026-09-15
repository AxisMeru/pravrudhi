'use strict';
const { contextBridge, ipcRenderer } = require('electron');
contextBridge.exposeInMainWorld('desktop', Object.freeze({
  health: () => ipcRenderer.invoke('engine:health'),
  updateState: () => ipcRenderer.invoke('engine:update-state'),
  openEngine: () => ipcRenderer.invoke('engine:open'),
  engineStatus: () => ipcRenderer.invoke('engine:status'),
  locateEngine: () => ipcRenderer.invoke('engine:locate'),
  restart: () => ipcRenderer.invoke('engine:restart'),
  stop: () => ipcRenderer.invoke('engine:stop'),
  doctor: () => ipcRenderer.invoke('engine:doctor'),
  checkForUpdates: () => ipcRenderer.invoke('engine:updates'),
  openWorkspace: () => ipcRenderer.invoke('engine:workspace'),
  // The bring-your-own-key surface (renderer/product.js): a provider id and key are a user's own, entered by
  // hand, so unlike every channel above these three forward what the renderer supplies rather than taking none.
  providers: () => ipcRenderer.invoke('providers:list'),
  validateProvider: (id) => ipcRenderer.invoke('providers:validate', id),
  setProviderKey: (id, key, baseUrl) => ipcRenderer.invoke('providers:key:set', id, key, baseUrl),
  deleteProviderKey: (id) => ipcRenderer.invoke('providers:key:delete', id),
  // Studio only: the operator's own message, forwarded the same way as the provider surface above. main.js
  // registers 'chat:send' only when the edition is Studio's own build, so this channel exists in every build
  // but answers with "no handler registered" in the product rather than ever reaching the engine's chat route.
  sendChatMessage: (message, threadId) => ipcRenderer.invoke('chat:send', message, threadId)
}));
