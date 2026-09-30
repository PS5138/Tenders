import type { Metadata } from 'next';
import { cookies } from 'next/headers';
import './globals.css';

export const metadata: Metadata = {
  title: { default: 'Ten', template: '%s · Ten' },
  description: 'Collaborative tender responses from approved company evidence.',
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: LayoutProps<'/'>) {
  const theme = (await cookies()).get('theme')?.value;
  return (
    <html lang="en-GB" data-theme={theme === 'light' || theme === 'dark' ? theme : undefined} className="h-full">
      <body className="min-h-full">{children}</body>
    </html>
  );
}
