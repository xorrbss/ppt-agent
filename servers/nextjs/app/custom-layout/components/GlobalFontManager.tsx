import React, { useState, useRef } from "react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import {
  Upload,
  CheckCircle,
  AlertCircle,
  X,
  Loader2,
  Type,
} from "lucide-react";

interface UploadedFont {
  fontName: string;
  fontUrl: string;
  fontPath: string;
}

interface GlobalFontManagerProps {
  slides: any[];
  globalUploadedFonts: UploadedFont[];
  uploadFont: (fontName: string, file: File) => Promise<string | null>;
  removeFont: (fontUrl: string) => void;
  getAllUnsupportedFonts: (slides: any[]) => string[];
}

const GlobalFontManager: React.FC<GlobalFontManagerProps> = ({
  slides,
  globalUploadedFonts,
  uploadFont,
  removeFont,
  getAllUnsupportedFonts,
}) => {
  const [uploadingFonts, setUploadingFonts] = useState<Set<string>>(new Set());
  const fileInputRefs = useRef<{ [key: string]: HTMLInputElement | null }>({});

  const allUnsupportedFonts = getAllUnsupportedFonts(slides);

  // Filter out fonts that are already uploaded
  const fontsNeedingUpload = allUnsupportedFonts.filter(
    (fontName) =>
      !globalUploadedFonts.some(
        (uploadedFont) => uploadedFont.fontName === fontName
      )
  );

  const handleFontUpload = async (fontName: string, file: File) => {
    if (!file) return;

    setUploadingFonts((prev) => new Set(prev).add(fontName));

    try {
      const fontUrl = await uploadFont(fontName, file);

      if (fontUrl) {
        // Clear the file input
        if (fileInputRefs.current[fontName]) {
          fileInputRefs.current[fontName]!.value = "";
        }
      }
    } finally {
      setUploadingFonts((prev) => {
        const newSet = new Set(prev);
        newSet.delete(fontName);
        return newSet;
      });
    }
  };

  const handleFileInputChange = (
    fontName: string,
    event: React.ChangeEvent<HTMLInputElement>
  ) => {
    const file = event.target.files?.[0];
    if (file) {
      handleFontUpload(fontName, file);
    }
  };

  const getFontUsageCount = (fontName: string): number => {
    return slides.filter((slide) =>
      slide.fonts?.not_supported_fonts?.includes(fontName)
    ).length;
  };

  if (allUnsupportedFonts.length === 0 && globalUploadedFonts.length === 0) {
    return null;
  }

  return (
    <Card className="mb-6">
      <CardHeader>
        <CardTitle className="text-xl flex items-center gap-2">
          <Type className="w-6 h-6" />
          Global Font Management
        </CardTitle>
        <p className="text-sm text-gray-600">
          Manage fonts across all slides. Upload fonts once and they'll be
          available for all slides.
        </p>
      </CardHeader>
      <CardContent className="space-y-6">
        {/* Fonts Needing Upload */}
        {fontsNeedingUpload.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-orange-700 mb-3 flex items-center gap-1">
              <AlertCircle className="w-4 h-4" />
              Fonts Needing Upload ({fontsNeedingUpload.length})
            </h4>
            <div className="space-y-3">
              {fontsNeedingUpload.map((fontName: string, index: number) => (
                <div
                  key={index}
                  className="p-4 bg-orange-50 border border-orange-200 rounded-lg"
                >
                  <div className="flex items-center justify-between">
                    <div>
                      <span className="text-sm font-medium text-orange-800">
                        {fontName}
                      </span>
                      <p className="text-xs text-orange-600 mt-1">
                        Used in {getFontUsageCount(fontName)} slide
                        {getFontUsageCount(fontName) !== 1 ? "s" : ""}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <input
                        ref={(el) => {
                          fileInputRefs.current[fontName] = el;
                        }}
                        type="file"
                        accept=".ttf,.otf,.woff,.woff2,.eot"
                        onChange={(e) => handleFileInputChange(fontName, e)}
                        className="hidden"
                        id={`global-font-upload-${index}`}
                      />
                      <Button
                        size="sm"
                        variant="outline"
                        disabled={uploadingFonts.has(fontName)}
                        onClick={() => fileInputRefs.current[fontName]?.click()}
                        className="text-xs bg-blue-600 text-white hover:bg-blue-700 border-blue-600"
                      >
                        {uploadingFonts.has(fontName) ? (
                          <>
                            <Loader2 className="w-3 h-3 mr-1 animate-spin" />
                            Uploading...
                          </>
                        ) : (
                          <>
                            <Upload className="w-3 h-3 mr-1" />
                            Upload Font
                          </>
                        )}
                      </Button>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {/* Successfully Uploaded Fonts */}
        {globalUploadedFonts.length > 0 && (
          <div>
            <h4 className="text-sm font-medium text-green-700 mb-3 flex items-center gap-1">
              <CheckCircle className="w-4 h-4" />
              Uploaded Fonts ({globalUploadedFonts.length})
            </h4>
            <div className="space-y-2">
              {globalUploadedFonts.map((font, index) => {
                const usageCount = getFontUsageCount(font.fontName);
                return (
                  <div
                    key={index}
                    className="p-3 bg-green-50 border border-green-200 rounded-lg flex items-center justify-between"
                  >
                    <div>
                      <span className="text-sm font-medium text-green-800">
                        {font.fontName}
                      </span>
                      <p className="text-xs text-green-600 mt-1">
                        {usageCount > 0 ? (
                          <>
                            Used in {usageCount} slide
                            {usageCount !== 1 ? "s" : ""}
                          </>
                        ) : (
                          <>Available for use</>
                        )}
                      </p>
                    </div>
                    <Button
                      size="sm"
                      variant="ghost"
                      onClick={() => removeFont(font.fontUrl)}
                      className="text-red-600 hover:text-red-700 hover:bg-red-50 p-1"
                    >
                      <X className="w-3 h-3" />
                    </Button>
                  </div>
                );
              })}
            </div>
          </div>
        )}

        {/* Summary */}
        <div className="pt-4 border-t border-gray-200">
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-center">
            <div className="p-3 bg-gray-50 rounded-lg">
              <p className="text-2xl font-bold text-gray-900">
                {allUnsupportedFonts.length}
              </p>
              <p className="text-xs text-gray-600">Total Unique Fonts</p>
            </div>
            <div className="p-3 bg-green-50 rounded-lg">
              <p className="text-2xl font-bold text-green-700">
                {globalUploadedFonts.length}
              </p>
              <p className="text-xs text-green-600">Fonts Uploaded</p>
            </div>
            <div className="p-3 bg-orange-50 rounded-lg">
              <p className="text-2xl font-bold text-orange-700">
                {fontsNeedingUpload.length}
              </p>
              <p className="text-xs text-orange-600">Fonts Needed</p>
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
};

export default GlobalFontManager;
