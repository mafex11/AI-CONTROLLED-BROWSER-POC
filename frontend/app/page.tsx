"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { Select } from "@/components/ui/select";
import { Mic, Send, Loader2, Zap, ChevronLeft, ChevronRight, ChevronDown, Square } from "lucide-react";
import { NarrationDisplay } from "@/components/narration-display";
import { ScreenshotDisplay } from "@/components/screenshot-display";
import { useVoiceWebSocket } from "@/components/voice-websocket";
import { Chat, ChatMessage } from "@/components/chat";

interface StepData {
  step: number;
  narration: string;
  screenshot?: string;
  timestamp: Date;
}

type LLMProvider = "gemini" | "claude" | "openai";

const PROVIDER_INFO: Record<LLMProvider, { label: string; speed: string }> = {
  gemini: { label: "Gemini", speed: "~2s per call" },
  claude: { label: "Claude", speed: "~3.75s per call" },
  openai: { label: "OpenAI", speed: "~4s per call" },
};

export default function Home() {
  const [query, setQuery] = useState("");
  const [provider, setProvider] = useState<LLMProvider>("gemini");
  const [isVoiceMode, setIsVoiceMode] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [steps, setSteps] = useState<StepData[]>([]);
  const [voiceStatus, setVoiceStatus] = useState<string>("");
  const [isSubmitted, setIsSubmitted] = useState(false);
  const [currentSlideIndex, setCurrentSlideIndex] = useState(0);
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>([]);
  const [processedSteps, setProcessedSteps] = useState<Set<string>>(new Set());
  const [isProviderDropdownOpen, setIsProviderDropdownOpen] = useState(false);
  const providerDropdownRef = useRef<HTMLDivElement>(null);
  const abortControllerRef = useRef<AbortController | null>(null);
  const readerRef = useRef<ReadableStreamDefaultReader<Uint8Array> | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const isPlayingAudioRef = useRef(false);

  // Initialize AudioContext for voice mode
  useEffect(() => {
    if (isVoiceMode && !audioContextRef.current) {
      const audioContext = new (window.AudioContext || (window as any).webkitAudioContext)();
      audioContextRef.current = audioContext;
    }
    
    return () => {
      if (audioContextRef.current) {
        audioContextRef.current.close().catch(() => {});
        audioContextRef.current = null;
      }
    };
  }, [isVoiceMode]);

  // Play audio chunks as they arrive
  const playAudioChunk = useCallback(async (audioBase64: string, sampleRate: number, numChannels: number) => {
    if (!audioContextRef.current) {
      return;
    }

    try {
      // Decode base64 to binary string, then to Uint8Array
      const binaryString = atob(audioBase64);
      const audioData = new Uint8Array(binaryString.length);
      for (let i = 0; i < binaryString.length; i++) {
        audioData[i] = binaryString.charCodeAt(i);
      }
      
      // Resume AudioContext if suspended (required for user interaction)
      if (audioContextRef.current.state === 'suspended') {
        await audioContextRef.current.resume();
      }

      // Create AudioBuffer from PCM data
      // PCM16 = 2 bytes per sample, so total samples = bytes / 2
      const samplesPerChannel = audioData.length / (2 * numChannels);
      const audioBuffer = audioContextRef.current.createBuffer(numChannels, samplesPerChannel, sampleRate);
      
      // Convert PCM16 bytes to Float32 samples
      const dataView = new DataView(audioData.buffer);
      for (let channel = 0; channel < numChannels; channel++) {
        const channelData = audioBuffer.getChannelData(channel);
        for (let i = 0; i < samplesPerChannel; i++) {
          // Interleaved PCM: sample format is [L, R, L, R, ...] for stereo
          const byteIndex = (i * numChannels + channel) * 2;
          const int16 = dataView.getInt16(byteIndex, true); // little-endian
          channelData[i] = int16 / 32768.0; // Convert to [-1, 1] range
        }
      }

      // Create AudioBufferSourceNode and play
      const source = audioContextRef.current.createBufferSource();
      source.buffer = audioBuffer;
      source.connect(audioContextRef.current.destination);
      
      source.onended = () => {
        isPlayingAudioRef.current = false;
      };

      isPlayingAudioRef.current = true;
      source.start();
    } catch (error) {
      console.error('Error playing audio chunk:', error);
    }
  }, []);

  const { isConnected: isVoiceConnected, isConnecting: isVoiceConnecting, disconnect: disconnectVoice } = useVoiceWebSocket({
    isEnabled: isVoiceMode,
    onUserSpeech: (text) => {
      if (!text.trim()) return;
      
      setVoiceStatus(`You: ${text}`);
      
      // Set submitted state to show chat
      if (!isSubmitted) {
        setIsSubmitted(true);
      }
      
      // Reset processed steps for new query
      setProcessedSteps(new Set());
      
      // Add user message to chat and thinking message
      setChatMessages((prev) => {
        // Check if we already have a thinking message (processing in progress)
        const hasThinking = prev.some(msg => msg.isLoading && msg.type === "agent");
        
        if (!hasThinking) {
          return [
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
          ];
        } else {
          // Update the last user message if we're already processing
          let lastUserIndex = -1;
          for (let i = prev.length - 1; i >= 0; i--) {
            if (prev[i].type === "user") {
              lastUserIndex = i;
              break;
            }
          }
          
          if (lastUserIndex >= 0) {
            const updated = [...prev];
            updated[lastUserIndex] = {
              ...updated[lastUserIndex],
              content: text,
              timestamp: new Date(),
            };
            return updated;
          }
          // If no user message found, add one
          return [
            ...prev,
            {
              id: `user-${Date.now()}`,
              type: "user",
              content: text,
              timestamp: new Date(),
            },
          ];
        }
      });
    },
    onAgentResponse: (text) => {
      if (!text.trim()) return;
      
      setVoiceStatus(`Agent: ${text}`);
      
      // Remove thinking message and add agent response if it's a standalone response
      // (Note: step messages are handled separately in onStep)
      setChatMessages((prev) => {
        const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
        
        // Only add if it's not already a step message
        const isStepMessage = filtered.some(
          (msg) => msg.type === "agent" && msg.content === text
        );
        
        if (!isStepMessage) {
          return [
            ...filtered,
            {
              id: `agent-response-${Date.now()}`,
              type: "agent",
              content: text,
              timestamp: new Date(),
            },
          ];
        }
        
        return filtered;
      });
    },
    onStep: (stepData) => {
      // Add step with narration and screenshot
      setSteps((prev) => {
        // Check if step already exists (update it) or add new one
        const existingIndex = prev.findIndex((s) => s.step === stepData.step);
        const isNewStep = existingIndex < 0;
        
        if (existingIndex >= 0) {
          const updated = [...prev];
          updated[existingIndex] = {
            ...updated[existingIndex],
            narration: stepData.narration || updated[existingIndex].narration,
            screenshot: stepData.screenshot || updated[existingIndex].screenshot,
          };
          return updated;
        }
        const newSteps = [
          ...prev,
          {
            step: stepData.step,
            narration: stepData.narration,
            screenshot: stepData.screenshot,
            timestamp: new Date(),
          },
        ];
        // Auto-advance to the new slide
        setCurrentSlideIndex(newSteps.length - 1);

        return newSteps;
      });

      // Always add agent response to chat if there's narration
      // This ensures all responses from voice mode are displayed
      if (stepData.narration && stepData.narration.trim()) {
        const stepKey = `${stepData.step}-${stepData.narration}-${Date.now()}`;
        
        setProcessedSteps((prev) => {
          // Use a more unique key to allow same step with different narrations
          const uniqueKey = `${stepData.step}-${stepData.narration}`;
          if (prev.has(uniqueKey)) {
            return prev; // Already processed this exact step/narration combo
          }
          const newSet = new Set(prev);
          newSet.add(uniqueKey);
          
          // Add message to chat when we add to processed steps
          setChatMessages((msgPrev) => {
            // Remove thinking message if it exists
            const filtered = msgPrev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
            
            // Check if we already have this exact message (same content)
            // Allow same narration but with different timestamps to handle updates
            const messageExists = filtered.some(
              (msg) => msg.type === "agent" && 
                       msg.content === stepData.narration && 
                       msg.id.startsWith(`agent-${stepData.step}-`)
            );
            
            if (messageExists) {
              // Update existing message with new screenshot if available
              return filtered.map(msg => 
                msg.type === "agent" && 
                msg.content === stepData.narration && 
                msg.id.startsWith(`agent-${stepData.step}-`)
                  ? { ...msg, screenshot: stepData.screenshot || msg.screenshot }
                  : msg
              );
            }
            
            return [
              ...filtered,
              {
                id: `agent-${stepData.step}-${Date.now()}`,
                type: "agent",
                content: stepData.narration,
                screenshot: stepData.screenshot,
                timestamp: new Date(),
              },
            ];
          });
          
          return newSet;
        });
      }
    },
    onError: (error) => {
      setVoiceStatus(`Error: ${error}`);
    },
    onAudioChunk: (audioBase64, sampleRate, numChannels) => {
      // Play audio chunk as it arrives
      playAudioChunk(audioBase64, sampleRate, numChannels);
    },
  });

  // Clear steps when voice mode is toggled
  useEffect(() => {
    if (isVoiceMode && isVoiceConnected) {
      setSteps([]);
      setCurrentSlideIndex(0);
    }
  }, [isVoiceMode, isVoiceConnected]);

  // Close provider dropdown when clicking outside
  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      if (providerDropdownRef.current && !providerDropdownRef.current.contains(event.target as Node)) {
        setIsProviderDropdownOpen(false);
      }
    };

    if (isProviderDropdownOpen) {
      document.addEventListener("mousedown", handleClickOutside);
    }

    return () => {
      document.removeEventListener("mousedown", handleClickOutside);
    };
  }, [isProviderDropdownOpen]);

  // Cleanup browser when tab closes
  useEffect(() => {
    const handleBeforeUnload = () => {
      // Send cleanup signal to backend using sendBeacon (more reliable for unload)
      try {
        // sendBeacon only accepts strings/Blob/FormData, not JSON directly
        const blob = new Blob([JSON.stringify({})], { type: "application/json" });
        navigator.sendBeacon("/api/cleanup", blob);
      } catch (e) {
        // Fallback if sendBeacon fails
        fetch("/api/cleanup", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
          keepalive: true,
        }).catch(() => {
          // Ignore errors during cleanup
        });
      }
    };

    window.addEventListener("beforeunload", handleBeforeUnload);
    
    return () => {
      window.removeEventListener("beforeunload", handleBeforeUnload);
      // Also send cleanup on component unmount
      fetch("/api/cleanup", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({}),
        keepalive: true, // Ensures request completes even if tab closes
      }).catch(() => {
        // Ignore errors during cleanup
      });
    };
  }, []);

  const handleStop = () => {
    if (isVoiceMode) {
      // Stop voice mode processing
      if (disconnectVoice) {
        disconnectVoice();
      }
      
      // Remove thinking message and add "stopped by user" message
      setChatMessages((prev) => {
        const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
        return [
          ...filtered,
          {
            id: `agent-stopped-${Date.now()}`,
            type: "agent",
            content: "Stopped by user",
            timestamp: new Date(),
          },
        ];
      });
    } else {
      // Abort the ongoing text request
      if (abortControllerRef.current) {
        abortControllerRef.current.abort();
        abortControllerRef.current = null;
      }
      
      // Cancel the reader if it exists
      if (readerRef.current) {
        readerRef.current.cancel().catch(() => {
          // Ignore cancellation errors
        });
        readerRef.current = null;
      }
      
      setIsLoading(false);
      
      // Remove thinking message and add "stopped by user" message
      setChatMessages((prev) => {
        const filtered = prev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
        return [
          ...filtered,
          {
            id: `agent-stopped-${Date.now()}`,
            type: "agent",
            content: "Stopped by user",
            timestamp: new Date(),
          },
        ];
      });
    }
  };
  
  // Check if voice mode is processing (has thinking message or recent activity)
  const isVoiceProcessing = isVoiceMode && chatMessages.some(
    (msg) => msg.isLoading && msg.type === "agent"
  );
  
  // Determine if we should show stop button
  const shouldShowStop = !isVoiceMode ? isLoading : isVoiceProcessing;

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!query.trim() || isLoading) return;
    
    // In voice mode, don't submit via text - voice handles it
    if (isVoiceMode) {
      return;
    }

    const userQuery = query.trim();
    setIsSubmitted(true);
    setIsLoading(true);
    setQuery("");
    // Reset processed steps for new query
    setProcessedSteps(new Set());

    // Add user query to chat and thinking message
    setChatMessages((prev) => [
      ...prev,
      {
        id: `user-${Date.now()}`,
        type: "user",
        content: userQuery,
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

    // Create new AbortController for this request
    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          query: userQuery,
          voiceMode: isVoiceMode,
          provider: provider,
        }),
        signal: abortController.signal,
      });

      if (!response.ok) {
        throw new Error("Failed to process query");
      }

      const reader = response.body?.getReader();
      if (!reader) {
        throw new Error("No response body");
      }

      readerRef.current = reader;
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
                setSteps((prev) => {
                  const newSteps = [
                    ...prev,
                    {
                      step: data.step,
                      narration: data.narration || "",
                      screenshot: data.screenshot,
                      timestamp: new Date(),
                    },
                  ];
                  // Auto-advance to the new slide
                  setCurrentSlideIndex(newSteps.length - 1);
                  return newSteps;
                });

                // Add agent response to chat if there's narration
                if (data.narration) {
                  const stepKey = `${data.step}-${data.narration}`;
                  
                  setProcessedSteps((prev) => {
                    if (prev.has(stepKey)) {
                      return prev; // Already processed this step
                    }
                    const newSet = new Set(prev);
                    newSet.add(stepKey);
                    
                    // Add message to chat when we add to processed steps
                    setChatMessages((msgPrev) => {
                      // Remove thinking message if it exists
                      const filtered = msgPrev.filter((msg) => !(msg.isLoading && msg.type === "agent"));
                      
                      // Check if we already have this exact message (same content and step)
                      const messageExists = filtered.some(
                        (msg) => msg.type === "agent" && msg.content === data.narration && msg.id.startsWith(`agent-${data.step}-`)
                      );
                      
                      if (messageExists) {
                        return filtered; // Already have this message
                      }
                      
                      return [
                        ...filtered,
                        {
                          id: `agent-${data.step}-${Date.now()}`,
                          type: "agent",
                          content: data.narration,
                          screenshot: data.screenshot,
                          timestamp: new Date(),
                        },
                      ];
                    });
                    
                    return newSet;
                  });
                }
              } else if (data.type === "complete") {
                // Query completed
              }
            } catch (e) {
              console.error("Failed to parse SSE data:", e);
            }
          }
        }
      }
    } catch (error: any) {
      // Ignore abort errors (user intentionally stopped)
      if (error.name === "AbortError") {
        return;
      }
      console.error("Error processing query:", error);
      // Remove thinking message on error
      setChatMessages((prev) => prev.filter((msg) => !(msg.isLoading && msg.type === "agent")));
    } finally {
      setIsLoading(false);
      abortControllerRef.current = null;
      readerRef.current = null;
    }
  };

  return (
    <div className="min-h-screen bg-zinc-900 flex flex-col relative">
      <div className={`max-w-6xl mx-auto w-full flex-1 flex flex-col ${isSubmitted || steps.length > 0 ? '' : 'justify-center'} px-4 md:px-8 pt-4 md:pt-8 pb-0`}>
        {!isSubmitted && (
          <div className="flex flex-col items-center justify-center space-y-8 mb-8">
            <AnimatePresence>
              <motion.div
                key="titles"
                initial={{ opacity: 0, y: -20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.4, ease: [0.4, 0, 0.2, 1] }}
                className="text-center"
              >
                <motion.h1 
                  className="text-5xl md:text-6xl font-bold mb-4 bg-gradient-to-r from-foreground to-foreground/70 bg-clip-text text-transparent"
                  initial={{ opacity: 0, scale: 0.95 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ delay: 0.1 }}
                >
                  AI Browser
                </motion.h1>
                <motion.p 
                  className="text-lg text-muted-foreground max-w-2xl mx-auto"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ delay: 0.2 }}
                >
                  Control your browser with natural language
                </motion.p>
              </motion.div>
            </AnimatePresence>
            
            {/* Input form in center on initial load */}
            <motion.div
              layout
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ 
                duration: 0.6, 
                ease: [0.4, 0, 0.2, 1],
              }}
              className="w-full max-w-4xl"
            >
              <motion.form
                layout
                onSubmit={handleSubmit}
                className="space-y-4"
              >
                <Card className="border-2 bg-transparent backdrop-blur-sm rounded-full">
                  <CardContent className="p-4">
                    <div className="flex items-center gap-3">
                      {/* Provider Selector as Custom Dropdown */}
                      <div className="relative" ref={providerDropdownRef}>
                        <button
                          type="button"
                          onClick={() => setIsProviderDropdownOpen(!isProviderDropdownOpen)}
                          disabled={isLoading}
                          className="h-12 px-8 rounded-full bg-zinc-900 border text-sm font-medium text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                        >
                          <span>
                            {PROVIDER_INFO[provider].label}
                            {provider === "gemini" && " (Fastest)"}
                            {provider === "claude" && " (Best)"}
                            {provider === "openai" && " (Good)"}
                          </span>
                          <ChevronDown className={`h-4 w-4 transition-transform ${isProviderDropdownOpen ? "rotate-180" : ""}`} />
                        </button>
                        <AnimatePresence>
                          {isProviderDropdownOpen && (
                            <motion.div
                              initial={{ opacity: 0, y: 10 }}
                              animate={{ opacity: 1, y: 0 }}
                              exit={{ opacity: 0, y: 10 }}
                              transition={{ duration: 0.2 }}
                              className="absolute bottom-full left-0 mb-2 w-full min-w-[200px] bg-zinc-900 border rounded-xl shadow-lg z-50 overflow-hidden"
                            >
                              {Object.entries(PROVIDER_INFO).map(([key, info]) => (
                                <button
                                  key={key}
                                  type="button"
                                  onClick={() => {
                                    setProvider(key as LLMProvider);
                                    setIsProviderDropdownOpen(false);
                                  }}
                                  disabled={isLoading}
                                  className={`w-full px-3 py-2.5 text-left text-sm font-medium transition-colors first:rounded-t-xl last:rounded-b-xl ${
                                    provider === key
                                      ? "bg-primary/20 text-primary"
                                      : "text-muted-foreground hover:bg-zinc-800"
                                  } disabled:opacity-50 disabled:cursor-not-allowed`}
                                >
                                  {info.label}
                                  {key === "gemini" && " (Fastest)"}
                                  {key === "claude" && " (Best)"}
                                  {key === "openai" && " (Good)"}
                                </button>
                              ))}
                            </motion.div>
                          )}
                        </AnimatePresence>
                      </div>

                      {/* Input */}
                      <div className="flex-1 relative ml-4 rounded-full">
                        <Input
                          type="text"
                          placeholder="What would you like me to do in the browser?"
                          value={query}
                          onChange={(e) => setQuery(e.target.value)}
                          disabled={isLoading}
                          className="h-12 text-base pr-12 border-2 focus-visible:ring-2 focus-visible:ring-primary/50 rounded-full"
                          onKeyDown={(e) => {
                            if (e.key === 'Enter' && !e.shiftKey && query.trim() && !isLoading) {
                              e.preventDefault();
                              handleSubmit(e as any);
                            }
                          }}
                        />
                      </div>

                      {/* Voice Mode Button */}
                      <Button
                        type="button"
                        variant={isVoiceMode ? "default" : "outline"}
                        size="icon"
                        onClick={() => setIsVoiceMode(!isVoiceMode)}
                        disabled={isLoading}
                        className="h-12 w-12 shrink-0 rounded-full"
                      >
                        <Mic className={`h-5 w-5 ${isVoiceMode ? 'animate-pulse' : ''}`} />
                      </Button>

                      {/* Submit/Stop Button */}
                      {shouldShowStop ? (
                        <Button
                          type="button"
                          onClick={handleStop}
                          className="h-12 px-6 shrink-0 rounded-full"
                          size="lg"
                        >
                          <Square className="h-5 w-5 mr-2" />
                          Stop
                        </Button>
                      ) : (
                        <Button
                          type="submit"
                          disabled={!query.trim() || isLoading}
                          className="h-12 px-6 shrink-0 rounded-full"
                          size="lg"
                        >
                          <Send className="h-5 w-5 mr-2" />
                          Send
                        </Button>
                      )}
                    </div>
                    {/* Voice Mode Status */}
                    {isVoiceMode && (
                      <motion.div
                        initial={{ opacity: 0, height: 0 }}
                        animate={{ opacity: 1, height: 'auto' }}
                        exit={{ opacity: 0, height: 0 }}
                        className="pt-2 border-t mt-4"
                      >
                        <div className="flex items-center gap-2 text-sm">
                          <div className={`h-2 w-2 rounded-full ${
                            isVoiceConnected ? 'bg-green-500 animate-pulse' : 
                            isVoiceConnecting ? 'bg-yellow-500 animate-pulse' : 
                            'bg-red-500'
                          }`} />
                          <span className="text-muted-foreground">
                            {isVoiceConnecting ? "Connecting..." : isVoiceConnected ? "Voice mode active" : "Voice mode disconnected"}
                          </span>
                        </div>
                        {voiceStatus && (
                          <motion.p 
                            initial={{ opacity: 0 }}
                            animate={{ opacity: 1 }}
                            className="text-xs text-muted-foreground mt-1.5 pl-4"
                          >
                            {voiceStatus}
                          </motion.p>
                        )}
                      </motion.div>
                    )}
                  </CardContent>
                </Card>
              </motion.form>
            </motion.div>
          </div>
        )}

        {isSubmitted && (
          <div className="flex-1 overflow-y-auto min-h-0 mb-4 flex flex-col">
            {chatMessages.length > 0 ? (
              <div className="flex-1 min-h-0">
                <Chat messages={chatMessages} />
              </div>
            ) : steps.length > 0 ? (
              <div className="flex-1 relative flex items-center justify-center">
                <AnimatePresence mode="wait">
                  {steps[currentSlideIndex] && (
                    <motion.div
                      key={currentSlideIndex}
                      initial={{ opacity: 0 }}
                      animate={{ opacity: 1 }}
                      exit={{ opacity: 0 }}
                      transition={{ duration: 0.5 }}
                      className="w-full space-y-4"
                    >
                      {steps[currentSlideIndex].narration && (
                        <NarrationDisplay
                          step={steps[currentSlideIndex].step}
                          narration={steps[currentSlideIndex].narration}
                        />
                      )}
                      {steps[currentSlideIndex].screenshot && (
                        <ScreenshotDisplay
                          step={steps[currentSlideIndex].step}
                          screenshot={steps[currentSlideIndex].screenshot}
                        />
                      )}
                    </motion.div>
                  )}
                </AnimatePresence>

                {steps.length > 1 && (
                  <>
                    <Button
                      variant="outline"
                      size="icon"
                      className="absolute left-4 z-10 shadow-lg backdrop-blur-sm bg-background/80 hover:bg-background"
                      onClick={() => setCurrentSlideIndex((prev) => Math.max(0, prev - 1))}
                      disabled={currentSlideIndex === 0}
                    >
                      <ChevronLeft className="h-5 w-5" />
                    </Button>
                    <Button
                      variant="outline"
                      size="icon"
                      className="absolute right-4 z-10 shadow-lg backdrop-blur-sm bg-background/80 hover:bg-background"
                      onClick={() => setCurrentSlideIndex((prev) => Math.min(steps.length - 1, prev + 1))}
                      disabled={currentSlideIndex === steps.length - 1}
                    >
                      <ChevronRight className="h-5 w-5" />
                    </Button>
                  </>
                )}

                {steps.length > 1 && (
                  <div className="absolute bottom-6 left-1/2 transform -translate-x-1/2 flex gap-2 z-10 bg-background/80 backdrop-blur-sm px-4 py-2 rounded-full shadow-lg border">
                    {steps.map((_, index) => (
                      <button
                        key={index}
                        onClick={() => setCurrentSlideIndex(index)}
                        className={`h-2 rounded-full transition-all duration-300 ${
                          index === currentSlideIndex
                            ? "w-8 bg-primary shadow-md"
                            : "w-2 bg-muted-foreground/30 hover:bg-muted-foreground/50"
                        }`}
                        aria-label={`Go to slide ${index + 1}`}
                      />
                    ))}
                  </div>
                )}
              </div>
            ) : (
              <div className="flex-1 flex items-center justify-center">
                <motion.div
                  initial={{ opacity: 0, scale: 0.9 }}
                  animate={{ opacity: 1, scale: 1 }}
                  transition={{ duration: 0.3 }}
                  className="flex flex-col items-center justify-center gap-4"
                >
                  {isLoading ? (
                    <>
                      <Loader2 className="h-10 w-10 animate-spin text-primary" />
                      <p className="text-sm text-muted-foreground animate-pulse">Processing your request...</p>
                    </>
                  ) : (
                    <p className="text-muted-foreground">No steps to display</p>
                  )}
                </motion.div>
              </div>
            )}
          </div>
        )}

        {!isSubmitted && steps.length > 0 && (
          <div className="flex-1 overflow-y-auto min-h-0 mb-4">
            <AnimatePresence mode="popLayout">
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                className="space-y-4 mt-6"
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
            </AnimatePresence>
          </div>
        )}

        {!isSubmitted && isLoading && steps.length === 0 && (
          <motion.div
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            className="flex items-center justify-center py-12"
          >
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </motion.div>
        )}
      </div>
      
      {/* Input form at bottom when submitted */}
      {isSubmitted && (
        <motion.div
          layout
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ 
            duration: 0.6, 
            ease: [0.4, 0, 0.2, 1],
          }}
          className="sticky bottom-0 z-50 bg-transparent"
        >
          <div className="max-w-6xl mx-auto px-4 md:px-8 py-4">
            <motion.form
              layout
              onSubmit={handleSubmit}
              className="space-y-4"
            >
              <Card className="border-2 bg-transparent backdrop-blur-sm rounded-full">
                <CardContent className="p-4">
                  <div className="flex items-center gap-3">
                    {/* Provider Selector as Custom Dropdown */}
                    <div className="relative" ref={providerDropdownRef}>
                      <button
                        type="button"
                        onClick={() => setIsProviderDropdownOpen(!isProviderDropdownOpen)}
                        disabled={isLoading}
                        className="h-12 px-8 rounded-full bg-zinc-900 border text-sm font-medium text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 transition-all flex items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        <span>
                          {PROVIDER_INFO[provider].label}
                          {provider === "gemini" && " (Fastest)"}
                          {provider === "claude" && " (Best)"}
                          {provider === "openai" && " (Good)"}
                        </span>
                        <ChevronDown className={`h-4 w-4 transition-transform ${isProviderDropdownOpen ? "rotate-180" : ""}`} />
                      </button>
                      <AnimatePresence>
                        {isProviderDropdownOpen && (
                          <motion.div
                            initial={{ opacity: 0, y: 10 }}
                            animate={{ opacity: 1, y: 0 }}
                            exit={{ opacity: 0, y: 10 }}
                            transition={{ duration: 0.2 }}
                            className="absolute bottom-full left-0 mb-2 w-full min-w-[200px] bg-zinc-900 border rounded-xl shadow-lg z-50 overflow-hidden"
                          >
                            {Object.entries(PROVIDER_INFO).map(([key, info]) => (
                              <button
                                key={key}
                                type="button"
                                onClick={() => {
                                  setProvider(key as LLMProvider);
                                  setIsProviderDropdownOpen(false);
                                }}
                                disabled={isLoading}
                                className={`w-full px-3 py-2.5 text-left text-sm font-medium transition-colors first:rounded-t-xl last:rounded-b-xl ${
                                  provider === key
                                    ? "bg-primary/20 text-primary"
                                    : "text-muted-foreground hover:bg-zinc-800"
                                } disabled:opacity-50 disabled:cursor-not-allowed`}
                              >
                                {info.label}
                                {key === "gemini" && " (Fastest)"}
                                {key === "claude" && " (Best)"}
                                {key === "openai" && " (Good)"}
                              </button>
                            ))}
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>

                    {/* Input */}
                    <div className="flex-1 relative ml-4 rounded-full">
                      <Input
                        type="text"
                        placeholder="What would you like me to do in the browser?"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        disabled={isLoading}
                        className="h-12 text-base pr-12 border-2 focus-visible:ring-2 focus-visible:ring-primary/50 rounded-full"
                        onKeyDown={(e) => {
                          if (e.key === 'Enter' && !e.shiftKey && query.trim() && !isLoading) {
                            e.preventDefault();
                            handleSubmit(e as any);
                          }
                        }}
                      />
                    </div>

                    {/* Voice Mode Button */}
                    <Button
                      type="button"
                      variant={isVoiceMode ? "default" : "outline"}
                      size="icon"
                      onClick={() => setIsVoiceMode(!isVoiceMode)}
                      disabled={isLoading}
                      className="h-12 w-12 shrink-0 rounded-full"
                    >
                      <Mic className={`h-5 w-5 ${isVoiceMode ? 'animate-pulse' : ''}`} />
                    </Button>

                    {/* Submit/Stop Button */}
                    {shouldShowStop ? (
                      <Button
                        type="button"
                        onClick={handleStop}
                        className="h-12 px-6 shrink-0 rounded-full"
                        size="lg"
                      >
                        <Square className="h-5 w-5 mr-2" />
                        Stop
                      </Button>
                    ) : (
                      <Button
                        type="submit"
                        disabled={!query.trim() || isLoading}
                        className="h-12 px-6 shrink-0 rounded-full"
                        size="lg"
                      >
                        <Send className="h-5 w-5 mr-2" />
                        Send
                      </Button>
                    )}
                  </div>
                  {/* Voice Mode Status */}
                  {isVoiceMode && (
                    <motion.div
                      initial={{ opacity: 0, height: 0 }}
                      animate={{ opacity: 1, height: 'auto' }}
                      exit={{ opacity: 0, height: 0 }}
                      className="pt-2 border-t mt-4"
                    >
                      <div className="flex items-center gap-2 text-sm">
                        <div className={`h-2 w-2 rounded-full ${
                          isVoiceConnected ? 'bg-green-500 animate-pulse' : 
                          isVoiceConnecting ? 'bg-yellow-500 animate-pulse' : 
                          'bg-red-500'
                        }`} />
                        <span className="text-muted-foreground">
                          {isVoiceConnecting ? "Connecting..." : isVoiceConnected ? "Voice mode active" : "Voice mode disconnected"}
                        </span>
                      </div>
                      {voiceStatus && (
                        <motion.p 
                          initial={{ opacity: 0 }}
                          animate={{ opacity: 1 }}
                          className="text-xs text-muted-foreground mt-1.5 pl-4"
                        >
                          {voiceStatus}
                        </motion.p>
                      )}
                    </motion.div>
                  )}
                </CardContent>
              </Card>
            </motion.form>
          </div>
        </motion.div>
      )}
    </div>
  );
}

