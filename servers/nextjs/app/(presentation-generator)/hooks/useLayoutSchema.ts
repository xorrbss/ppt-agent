import { useState, useEffect } from "react";
import { toast } from "@/hooks/use-toast";
import * as z from 'zod';
interface LayoutInfo {
  id: string;
  name?: string;
  description?: string;
  json_schema: any;
}

// interface LayoutStructure {
//   name: string;
//   ordered: boolean;
//   slides: LayoutInfo[];
// }

const useLayoutSchema = () => {
  const [layoutSchema, setLayoutSchema] = useState<LayoutInfo[] | null>(null);
  const [idMapFileNames, setIdMapFileNames] = useState<Record<string, string>>({});
  const [idMapSchema, setIdMapSchema] = useState<Record<string, z.ZodSchema>>({});
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadLayouts = async () => {
    try {
      setLoading(true);
      setError(null);
      const layoutResponse = await fetch('/api/layouts');
      const layoutFiles = await layoutResponse.json();
      const response = await extractSchema(layoutFiles);
      
      setLayoutSchema(response?.layouts || []);
      setIdMapFileNames(response?.idMapFileNames || {});
      setIdMapSchema(response?.idMapSchema || {});
    } catch (err: unknown) {
      const errorMessage = err instanceof Error ? err.message : 'Failed to load layouts';
      setError(errorMessage);
      console.error('Error loading layouts:', err);
    } finally {
      setLoading(false);
    }
  };



  // Auto-load layouts on mount
  useEffect(() => {
    loadLayouts();
  }, []);

  return { 
    layoutSchema, 
    setLayoutSchema, 
    loading, 
    error, 
    refetch: loadLayouts,
    idMapFileNames,
    idMapSchema
  };
};

export default useLayoutSchema;


const extractSchema = async (layoutFiles: string[])  => {
  const layouts: LayoutInfo[] = [];
  const idMapFileNames: Record<string, string> = {};
  const idMapSchema: Record<string, z.ZodSchema> = {};
 for (const fileName of layoutFiles) {
  try {
    const file = fileName.replace('.tsx', '').replace('.ts', '')
    const module = await import(`@/components/layouts/${file}`)
    if (!module.default) {
      toast({
        title: `${file} has no default export`,
        description: 'Please ensure the layout file exports a default component',
      })
      console.warn(`${file} has no default export`)
      return
    }
    if (!module.Schema) {
      toast({
        title: `${file} has no Schema export`,
        description: 'Please ensure the layout file exports a Schema',
      })
      console.warn(`${file} has no Schema export`)
      return
    }
    const layoutId = module.layoutId
    if(!layoutId) {
      toast({
        title: `${file} has no layoutId`,
        description: 'Please ensure the layout file exports a layoutId',
      })
      console.warn(`${file} has no layoutId`)
      return
    }
    const layoutName = module.layoutName 
    const layoutDescription = module.layoutDescription 
    const jsonSchema = z.toJSONSchema(module.Schema,{
      override :(ctx)=>{
        delete ctx.jsonSchema.default
      },
    })
    const layout = {
      id: layoutId,
      name: layoutName,
      description: layoutDescription,
      json_schema: jsonSchema,
    }
    idMapFileNames[layoutId] = fileName
    idMapSchema[layoutId] = module.Schema
    layouts.push(layout)
  } catch (error) {
    console.error(`Error extracting schema for ${fileName}:`, error)
    return null
  }
 }
 return {layouts, idMapFileNames, idMapSchema}
};
