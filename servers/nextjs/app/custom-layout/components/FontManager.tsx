import React, { useState, useEffect } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { CheckCircle, AlertCircle, X } from "lucide-react";
import { toast } from "sonner";

interface UploadedFont {
  fontName: string;
  fontUrl: string;
  fontPath: string;
}

interface FontManagerProps {
  slide: any;
  onFontsUpdate: (updatedFonts: string[]) => void;
  globalUploadedFonts: UploadedFont[];
}

const FontManager: React.FC<FontManagerProps> = ({
  slide,
  onFontsUpdate,
  globalUploadedFonts,
}) => {
  const [slideSpecificFonts, setSlideSpecificFonts] = useState<string[]>(
    slide.uploaded_fonts || []
  );

  // Update slide-specific fonts when slide changes
  useEffect(() => {
    setSlideSpecificFonts(slide.uploaded_fonts || []);
  }, [slide.uploaded_fonts]);

  const addGlobalFontToSlide = (fontUrl: string) => {
    if (!slideSpecificFonts.includes(fontUrl)) {
      const updatedFonts = [...slideSpecificFonts, fontUrl];
      setSlideSpecificFonts(updatedFonts);
      onFontsUpdate(updatedFonts);
      toast.success("Font added to slide");
    }
  };

  const removeSlideFont = (fontUrl: string) => {
    const updatedFonts = slideSpecificFonts.filter((url) => url !== fontUrl);
    setSlideSpecificFonts(updatedFonts);
    onFontsUpdate(updatedFonts);
    toast.info("Font removed from slide");
  };

  const getFontNameFromUrl = (url: string) => {
    return url.split("/").pop()?.split(".")[0] || "Custom Font";
  };

  if (!slide.fonts) {
    return null;
  }

  const { internally_supported_fonts = [], not_supported_fonts = [] } =
    slide.fonts;

  // Get fonts that are globally uploaded but not in this slide
  const availableGlobalFonts = globalUploadedFonts.filter(
    (font) => !slideSpecificFonts.includes(font.fontUrl)
  );

  return (
    <Card className="mt-4">
      <CardHeader>
        <CardTitle className="text-lg flex items-center gap-2">
          <span>Slide Font Management</span>
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-4">
        {/* Supported Fonts */}
        {internally_supported_fonts.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-green-700 mb-2 flex items-center gap-1">
              <CheckCircle className="w-4 h-4" />
              Supported Fonts ({internally_supported_fonts.length})
            </h4>
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2">
              {internally_supported_fonts.map((font: any, index: number) => (
                <div
                  key={index}
                  className="p-2 bg-green-50 border border-green-200 rounded text-sm text-green-800"
                >
                  {font.name}
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Unsupported Fonts - only show if they're not globally uploaded */}
        {not_supported_fonts.filter(
          (fontName: string) =>
            !globalUploadedFonts.some((gf) => gf.fontName === fontName)
        ).length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-orange-700 mb-2 flex items-center gap-1">
              <AlertCircle className="w-4 h-4" />
              Missing Fonts (Upload in Global Font Manager above)
            </h4>
            <div className="space-y-2">
              {not_supported_fonts
                .filter(
                  (fontName: string) =>
                    !globalUploadedFonts.some((gf) => gf.fontName === fontName)
                )
                .map((fontName: string, index: number) => (
                  <div
                    key={index}
                    className="p-2 bg-orange-50 border border-orange-200 rounded text-sm text-orange-800"
                  >
                    {fontName} - Please upload in Global Font Manager
                  </div>
                ))}
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
};

export default FontManager;
