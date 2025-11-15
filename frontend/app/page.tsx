"use client";

import { useState, useEffect } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Mic, Send, Loader2 } from "lucide-react";
import { NarrationDisplay } from "@/components/narration-display";
import { ScreenshotDisplay } from "@/components/screenshot-display";
import { useVoiceWebSocket } from "@/components/voice-websocket";

interface StepData {
  step: number;
  narration: string;
  screenshot?: string;
  timestamp: Date;
}

export default function Home() {
  const [query, setQuery] = useState("");
  const [isVoiceMode, setIsVoiceMode] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [steps, setSteps] = useState<StepData[]>([]);
  const [voiceStatus, setVoiceStatus] = useState<string>("");

  const { isConnected: isVoiceConnected, isConnecting: isVoiceConnecting } = useVoiceWebSocket({
    isEnabled: isVoiceMode,
    onUserSpeech: (text) => {
      setVoiceStatus(`You: ${text}`);
    },
    onAgentResponse: (text) => {
      setVoiceStatus(`Agent: ${text}`);
    },
    onStep: (stepData) => {
      // Add step with narration and screenshot
      setSteps((prev) => {
        // Check if step already exists (update it) or add new one
        const existingIndex = prev.findIndex((s) => s.step === stepData.step);
        if (existingIndex >= 0) {
          const updated = [...prev];
          updated[existingIndex] = {
            ...updated[existingIndex],
            narration: stepData.narration || updated[existingIndex].narration,
            screenshot: stepData.screenshot || updated[existingIndex].screenshot,
          };
          return updated;
        }
        return [
          ...prev,
          {
            step: stepData.step,
            narration: stepData.narration,
            screenshot: stepData.screenshot,
            timestamp: new Date(),
          },
        ];
      });
    },
    onError: (error) => {
      setVoiceStatus(`Error: ${error}`);
    },
  });

  // Clear steps when voice mode is toggled
  useEffect(() => {
    if (isVoiceMode && isVoiceConnected) {
      setSteps([]);
    }
  }, [isVoiceMode, isVoiceConnected]);

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isLoading) return;
    
    // In voice mode, don't submit via text - voice handles it
    if (isVoiceMode) {
      return;
    }

    setIsLoading(true);
    setSteps([]);

    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: query.trim(),
          voiceMode: isVoiceMode,
        }),
      });

      if (!response.ok) {
        throw new Error("Failed to process query");
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error("No response body");
      }

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        for (const line of lines) {
          if (line.startsWith("data: ")) {
            try {
              const data = JSON.parse(line.slice(6));
              if (data.type === "step") {
                setSteps((prev) => [
                  ...prev,
                  {
                    step: data.step,
                    narration: data.narration || "",
                    screenshot: data.screenshot,
                    timestamp: new Date(),
                  },
                ]);
              } else if (data.type === "complete") {
                // Query completed
              }
            } catch (e) {
              console.error("Failed to parse SSE data:", e);
            }
          }
        }
      }
    } catch (error) {
      console.error("Error processing query:", error);
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="min-h-screen bg-background p-4 md:p-8">
      <div className="max-w-6xl mx-auto space-y-6">
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3 }}
        >
          <h1 className="text-4xl font-bold mb-2">AI Browser</h1>
          <p className="text-muted-foreground">
            Control your browser with natural language
          </p>
        </motion.div>

        <motion.form
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.3, delay: 0.1 }}
          onSubmit={handleSubmit}
          className="space-y-4"
        >
          <Card>
            <CardContent className="p-4">
              <div className="flex gap-2">
                <div className="flex-1 relative">
                  <Input
                    type="text"
                    placeholder="Enter your browser task..."
                    value={query}
                    onChange={(e) => setQuery(e.target.value)}
                    disabled={isLoading}
                    className="pr-12"
                  />
                </div>
                <Button
                  type="button"
                  variant={isVoiceMode ? "default" : "outline"}
                  size="icon"
                  onClick={() => setIsVoiceMode(!isVoiceMode)}
                  disabled={isLoading}
                >
                  <Mic className="h-4 w-4" />
                </Button>
                <Button
                  type="submit"
                  disabled={!query.trim() || isLoading}
                >
                  {isLoading ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Send className="h-4 w-4" />
                  )}
                </Button>
              </div>
              {isVoiceMode && (
                <motion.div
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  className="text-sm text-muted-foreground mt-2 space-y-1"
                >
                  <p>
                    Voice mode: {isVoiceConnecting ? "Connecting..." : isVoiceConnected ? "Connected" : "Disconnected"}
                  </p>
                  {voiceStatus && (
                    <p className="text-xs">{voiceStatus}</p>
                  )}
                </motion.div>
              )}
            </CardContent>
          </Card>
        </motion.form>

        <AnimatePresence mode="popLayout">
          {steps.length > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              exit={{ opacity: 0 }}
              className="space-y-4"
            >
              {steps.map((stepData, index) => (
                <motion.div
                  key={`${stepData.step}-${index}`}
                  initial={{ opacity: 0, y: 20 }}
                  animate={{ opacity: 1, y: 0 }}
                  transition={{ duration: 0.3, delay: index * 0.1 }}
                  className="space-y-4"
                >
                  {stepData.narration && (
                    <NarrationDisplay
                      step={stepData.step}
                      narration={stepData.narration}
                    />
                  )}
                  {stepData.screenshot && (
                    <ScreenshotDisplay
                      step={stepData.step}
                      screenshot={stepData.screenshot}
                    />
                  )}
                </motion.div>
              ))}
            </motion.div>
          )}
        </AnimatePresence>

        {isLoading && steps.length === 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center justify-center py-12"
          >
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </motion.div>
        )}
      </div>
    </div>
  );
}

