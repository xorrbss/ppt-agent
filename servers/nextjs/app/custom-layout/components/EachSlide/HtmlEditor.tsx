import React, { useState } from "react";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Save, X, Eye, Code } from "lucide-react";
import { ProcessedSlide } from "../../types";

interface HtmlEditorProps {
  slide: ProcessedSlide;
  isHtmlEditMode: boolean;
  onSave: (html: string) => void;
  onCancel: () => void;
}

export const HtmlEditor: React.FC<HtmlEditorProps> = ({
  slide,
  isHtmlEditMode,
  onSave,
  onCancel,
}) => {
  const [htmlContent, setHtmlContent] = useState(slide.html || "");
  const [isPreviewMode, setIsPreviewMode] = useState(false);

  if (!isHtmlEditMode) return null;

  const handleSave = () => {
    onSave(htmlContent);
  };

  const handleCancel = () => {
    setHtmlContent(slide.html || "");
    onCancel();
  };

  return (
    <Card className="border-2 border-purple-200 bg-purple-50">
      <CardHeader>
        <CardTitle className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Code className="w-5 h-5 text-purple-600" />
            <span className="text-purple-800">HTML Editor</span>
          </div>
          <div className="flex gap-2">
            <Button
              variant={isPreviewMode ? "default" : "outline"}
              size="sm"
              onClick={() => setIsPreviewMode(!isPreviewMode)}
              className="flex items-center gap-1"
            >
              <Eye size={14} />
              {isPreviewMode ? "Code" : "Preview"}
            </Button>
            <Button
              variant="outline"
              size="sm"
              onClick={handleCancel}
              className="flex items-center gap-1"
            >
              <X size={14} />
              Cancel
            </Button>
            <Button
              onClick={handleSave}
              className="flex items-center gap-1 bg-purple-600 hover:bg-purple-700"
            >
              <Save size={14} />
              Save HTML
            </Button>
          </div>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {isPreviewMode ? (
          <div className="border rounded-lg p-4 bg-white">
            <div
              className="prose max-w-none"
              dangerouslySetInnerHTML={{ __html: htmlContent }}
            />
          </div>
        ) : (
          <div className="space-y-2">
            <label className="text-sm font-medium text-gray-700">
              Edit HTML Content:
            </label>
            <Textarea
              value={htmlContent}
              onChange={(e) => setHtmlContent(e.target.value)}
              className="font-mono text-sm h-96 resize-none"
              placeholder="Enter HTML content here..."
            />
            <div className="text-xs text-gray-500">
              Tip: You can edit the HTML directly. Make sure to maintain proper HTML structure.
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}; 