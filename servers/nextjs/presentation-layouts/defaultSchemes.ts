import * as z from "zod";

export const ImageSchema = z.object({
    __image_url__: z.url().meta({
        description: "URL to image",
    }),
    __image_prompt__: z.string().meta({
        description: "Prompt used to generate the image",
    }),
})

export const IconSchema = z.object({
    __icon_url__: z.string().meta({
        description: "URL to icon",
    }),
    __icon_query__: z.string().meta({
        description: "Query used to search the icon",
    }),
})