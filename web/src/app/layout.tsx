import type { Metadata } from "next";
import { Nunito_Sans, Rubik } from "next/font/google";
import { Providers } from "@/components/providers";
import { Header } from "@/components/header";
import { Footer } from "@/components/footer";
import { CopilotActionsBridge } from "@/components/copilot-actions";
import "./globals.css";

const nunito = Nunito_Sans({
  subsets: ["latin"],
  weight: ["400", "600"],
  variable: "--font-nunito",
  display: "swap",
});

const rubik = Rubik({
  subsets: ["latin"],
  weight: ["600", "700"],
  variable: "--font-rubik",
  display: "swap",
});

export const metadata: Metadata = {
  title: "An Phát PC",
  description: "Cửa hàng máy tính, laptop, linh kiện PC — An Phát",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="vi" className={`${nunito.variable} ${rubik.variable}`}>
      <body className="min-h-screen">
        <Providers>
          <a
            href="#main"
            className="sr-only focus:not-sr-only focus:absolute focus:left-2 focus:top-2 focus:rounded focus:bg-white focus:p-2 focus:shadow"
          >
            Bỏ qua nội dung
          </a>
          <Header />
          <main id="main" className="container mx-auto px-4 py-6">
            {children}
          </main>
          <Footer />
          <CopilotActionsBridge />
        </Providers>
      </body>
    </html>
  );
}
