"use client";

import { useState } from "react";
import { ExperimentalWebRTCPanel } from "@/components/webrtc/experimental-panel";
import { BrowserScreenPanel } from "@/components/webrtc/browser-screen-panel";
import { ChatPanel } from "@/components/webrtc/chat-panel";
import { ChatMessage } from "@/components/chat";
import { useVoiceWebSocket } from "@/components/voice-websocket";

export default function WebRTCPage() {
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [isVoiceEnabled, setIsVoiceEnabled] = useState(false);

  const { isConnected, isConnecting } = useVoiceWebSocket({
    isEnabled: isVoiceEnabled,
    onUserSpeech: (text) => {
      if (!text.trim()) return;
      
      setChatMessages((prev) => [
        ...prev,
        {
          id: `user-${Date.now()}`,
          type: "user",
          content: text,
          timestamp: new Date(),
        },
        {
          id: `thinking-${Date.now()}`,
          type: "agent",
          content: "Thinking...",
          timestamp: new Date(),
          isLoading: true,
        },
      ]);
    },
    onAgentResponse: (text) => {
      if (!text.trim()) return;
      
      setChatMessages((prev) => {
        // Remove thinking message if it exists
        const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
        
        return [
          ...filtered,
          {
            id: `agent-${Date.now()}`,
            type: "agent",
            content: text,
            timestamp: new Date(),
          },
        ];
      });
    },
    onStep: (stepData) => {
      if (stepData.narration && stepData.narration.trim()) {
        setChatMessages((prev) => {
          // Remove thinking message if it exists
          const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
          
          // Check if we already have a message for this step
          const existingMessageIndex = filtered.findIndex(
            (msg) => msg.type === "agent" && msg.id.includes(`-${stepData.step}-`)
          );
          
          if (existingMessageIndex >= 0) {
            // Update existing message
            const updated = [...filtered];
            const existingMsg = updated[existingMessageIndex];
            
            if (existingMsg.content !== stepData.narration) {
              updated[existingMessageIndex] = {
                ...existingMsg,
                content: stepData.narration,
                timestamp: new Date(),
              };
            }
            
            return updated;
          }
          
          // Add new message
          return [
            ...filtered,
            {
              id: `agent-step-${stepData.step}-${Date.now()}`,
              type: "agent",
              content: stepData.narration,
              timestamp: new Date(),
            },
          ];
        });
      }
    },
    onError: (error) => {
      console.error("Voice WebSocket error:", error);
    },
  });

  return (
    <main className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-6">
      <ExperimentalWebRTCPanel />
      <BrowserScreenPanel />
      <ChatPanel 
        messages={chatMessages} 
        isVoiceEnabled={isVoiceEnabled}
        isVoiceConnected={isConnected}
        isVoiceConnecting={isConnecting}
        onToggleVoice={() => setIsVoiceEnabled(!isVoiceEnabled)}
        onClearChat={() => setChatMessages([])}
      />
    </main>
  );
}

