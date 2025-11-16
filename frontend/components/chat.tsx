"use client";

import { motion, AnimatePresence } from "framer-motion";
import { Card, CardContent } from "@/components/ui/card";
import { Loader2 } from "lucide-react";
import { useEffect, useRef } from "react";

export interface ChatMessage {
  id: string;
  type: "user" | "agent";
  content: string;
  screenshot?: string;
  timestamp: Date;
  isLoading?: boolean;
}

interface ChatProps {
  messages: ChatMessage[];
}

export function Chat({ messages }: ChatProps) {
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  return (
    <div className="h-full overflow-y-auto p-4 space-y-4">
      <AnimatePresence>
        {messages.map((message) => (
          <motion.div
            key={message.id}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
            className={`flex gap-3 ${
              message.type === "user" ? "justify-end" : "justify-start"
            }`}
          >
            <div
              className={`flex flex-col gap-2 max-w-[80%] ${
                message.type === "user" ? "items-end" : "items-start"
              }`}
            >
              <Card
                className={`${
                  message.type === "user"
                    ? "bg-primary text-primary-foreground"
                    : "bg-muted"
                }`}
              >
                <CardContent className="p-3">
                  {message.isLoading ? (
                    <div className="flex items-center gap-2">
                      <Loader2 className="h-4 w-4 animate-spin" />
                      <p className="text-sm">{message.content}</p>
                    </div>
                  ) : (
                    <p className="text-sm whitespace-pre-wrap">{message.content}</p>
                  )}
                </CardContent>
              </Card>
              {message.screenshot && message.type === "agent" && (
                <Card className="w-full">
                  <CardContent className="p-2">
                    <img
                      src={
                        message.screenshot.startsWith("data:image")
                          ? message.screenshot
                          : `data:image/png;base64,${message.screenshot}`
                      }
                      alt="Agent screenshot"
                      className="w-full h-auto rounded-md max-h-96 object-contain"
                    />
                  </CardContent>
                </Card>
              )}
            </div>
          </motion.div>
        ))}
      </AnimatePresence>
      <div ref={messagesEndRef} />
    </div>
  );
}

