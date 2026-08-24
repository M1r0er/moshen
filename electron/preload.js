/**
 * 墨参 MoShen · Preload 脚本
 * 在渲染进程暴露安全的 Electron API
 */
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('electronAPI', {
  selectDirectory: () => ipcRenderer.invoke('dialog:selectDirectory'),
});
