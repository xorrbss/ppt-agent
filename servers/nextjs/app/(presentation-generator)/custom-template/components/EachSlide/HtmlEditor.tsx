'use client'
import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Save, X, Code } from "lucide-react";
import { ProcessedSlide } from "../../types";
import Editor from 'react-simple-code-editor';
import { highlight, languages } from 'prismjs';
import 'prismjs/components/prism-clike';
import 'prismjs/components/prism-javascript';
import 'prismjs/components/prism-markup';
import 'prismjs/components/prism-jsx';

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

  useEffect(() => {
    setHtmlContent(slide.html || "");
  }, [slide.html]);

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
      <CardContent className="space-y-4 ">
          <div
      dangerouslySetInnerHTML={{
        __html: htmlContent,
      }}
    />
    <p className="text-base text-gray-800">Edit the HTML code to customize the slide layout.</p>
         {/* Render code editor */}
         <div className="container__content_area">

          <Editor
      value={htmlContent}
      onValueChange={htmlContent => setHtmlContent(htmlContent)}
      highlight={htmlContent => highlight(htmlContent, languages.jsx!,'jsx')}
      padding={10}
      id="html-editor"
      name="html-editor"
      
      className="container__editor"
      />
      </div>
     
    
      </CardContent>
    </Card>
  );
}; 