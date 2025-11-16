"use client";

import { motion } from "framer-motion";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Image as ImageIcon } from "lucide-react";
import { useState } from "react";

interface ScreenshotDisplayProps {
  step: number;
  screenshot: string;
}

export function ScreenshotDisplay({
  step,
  screenshot,
}: ScreenshotDisplayProps) {
  const [isExpanded, setIsExpanded] = useState(false);

  const imageSrc = screenshot.startsWith("data:image")
    ? screenshot
    : `data:image/png;base64,${screenshot}`;

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.2 }}
    >
      <Card>
        <CardHeader className="pb-3">
          <CardTitle className="text-lg flex items-center gap-2">
            <ImageIcon className="h-4 w-4" />
            View
          </CardTitle>
        </CardHeader>
        <CardContent>
          <motion.div
            className="relative overflow-hidden rounded-md border bg-muted cursor-pointer"
            onClick={() => setIsExpanded(!isExpanded)}
            whileHover={{ scale: 1.02 }}
            transition={{ duration: 0.2 }}
          >
            <motion.img
              src={imageSrc}
              alt="Screenshot view"
              className={`w-full h-auto ${
                isExpanded ? "max-h-none" : "max-h-96 object-contain"
              }`}
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3 }}
            />
          </motion.div>
          {!isExpanded && (
            <p className="text-xs text-muted-foreground mt-2 text-center">
              Click to expand
            </p>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}

