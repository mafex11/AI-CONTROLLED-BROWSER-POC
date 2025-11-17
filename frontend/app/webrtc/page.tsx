import { ExperimentalWebRTCPanel } from "@/components/webrtc/experimental-panel";
import { BrowserScreenPanel } from "@/components/webrtc/browser-screen-panel";

export default function WebRTCPage() {
  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-6">
      <ExperimentalWebRTCPanel />
      <BrowserScreenPanel />
    </main>
  );
}

