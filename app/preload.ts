import { contextBridge, ipcRenderer } from 'electron';

contextBridge.exposeInMainWorld('env', {
  NEXT_PUBLIC_FAST_API: process.env.NEXT_PUBLIC_FAST_API || '',
  NEXT_PUBLIC_URL: process.env.NEXT_PUBLIC_URL || '',
  TEMP_DIRECTORY: process.env.TEMP_DIRECTORY || '',
  NEXT_PUBLIC_USER_CONFIG_PATH: process.env.NEXT_PUBLIC_USER_CONFIG_PATH || '',
});


contextBridge.exposeInMainWorld('electron', {
  fileDownloaded: (filePath: string) => ipcRenderer.invoke("file-downloaded", filePath),
  getUserConfig: () => ipcRenderer.invoke("get-user-config"),
  setUserConfig: (userConfig: UserConfig) => ipcRenderer.invoke("set-user-config", userConfig),
  readFile: (filePath: string) => ipcRenderer.invoke("read-file", filePath),
  getSlideMetadata: (url: string, theme: string, customColors?: any) => ipcRenderer.invoke("get-slide-metadata", url, theme, customColors),
});
