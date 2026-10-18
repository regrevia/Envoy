import '../styles/globals.css';

export const metadata = {
  title: 'Envoy v1.0',
  description: 'Video-driven capability generation MVP',
  icons: {
    icon: '/brand/envoy-bird.png',
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body>{children}</body>
    </html>
  );
}
