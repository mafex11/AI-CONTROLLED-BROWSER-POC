"use client";

import { Card, CardContent, CardHeader, CardTitle } from "../ui/card";
import { Button } from "../ui/button";
import { Chat, ChatMessage } from "../chat";
import { Mic, MicOff, Trash2 } from "lucide-react";

interface ChatPanelProps {
  messages: ChatMessage[];
  isVoiceEnabled: boolean;
  isVoiceConnected: boolean;
  isVoiceConnecting: boolean;
  onToggleVoice: () => void;
  onClearChat: () => void;
}

export function ChatPanel({
  messages,
  isVoiceEnabled,
  isVoiceConnected,
  isVoiceConnecting,
  onToggleVoice,
  onClearChat,
}: ChatPanelProps) {
  return (
    <Card className="p-4">
      <CardHeader className="flex flex-row items-center justify-between space-y-0 pb-4">
        <div className="flex items-center gap-3">
          <CardTitle>Conversation</CardTitle>
          <div className="flex items-center gap-2 text-sm">
            <div
              className={`h-2 w-2 rounded-full ${
                isVoiceConnected
                  ? "bg-green-500 animate-pulse"
                  : isVoiceConnecting
                  ? "bg-yellow-500 animate-pulse"
                  : "bg-gray-500"
              }`}
            />
            <span className="text-muted-foreground text-xs">
              {isVoiceConnecting
                ? "Connecting..."
                : isVoiceConnected
                ? "Connected"
                : "Disconnected"}
            </span>
          </div>
        </div>
        <div className="flex gap-2">
          <Button
            variant={isVoiceEnabled ? "destructive" : "outline"}
            size="sm"
            onClick={onToggleVoice}
          >
            {isVoiceEnabled ? (
              <>
                <MicOff className="h-4 w-4 mr-2" />
                Stop Voice
              </>
            ) : (
              <>
                <Mic className="h-4 w-4 mr-2" />
                Start Voice
              </>
            )}
          </Button>
          {messages.length > 0 && (
            <Button variant="ghost" size="sm" onClick={onClearChat}>
              <Trash2 className="h-4 w-4 mr-2" />
              Clear
            </Button>
          )}
        </div>
      </CardHeader>
      <CardContent>
        <div className="h-96 overflow-y-auto border rounded-lg">
          {messages.length === 0 ? (
            <div className="flex items-center justify-center h-full text-muted-foreground text-sm">
              {isVoiceEnabled
                ? "Start speaking to begin the conversation..."
                : "Enable voice mode to start chatting"}
            </div>
          ) : (
            <Chat messages={messages} />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

