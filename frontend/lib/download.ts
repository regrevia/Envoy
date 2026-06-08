import { apiUrl } from '@/lib/api';

export async function downloadFile(path: string, filename?: string) {
  const response = await fetch(apiUrl(path));
  if (!response.ok) {
    throw new Error(`Download failed: HTTP ${response.status}`);
  }

  const blob = await response.blob();
  const objectUrl = window.URL.createObjectURL(blob);
  const anchor = document.createElement('a');
  anchor.href = objectUrl;
  anchor.download = filename || path.split('/').filter(Boolean).pop() || 'download';
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  window.URL.revokeObjectURL(objectUrl);
}

export function openDownloadUrl(path: string) {
  window.open(apiUrl(path), '_blank', 'noreferrer');
}
