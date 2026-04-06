import * as z from "zod";


export const slideLayoutId = "code-api-request-response-slide";
export const slideLayoutName = "Code API Request Response Slide";
export const slideLayoutDescription =
  "An API-focused slide with endpoint metadata, request payload, and response payload.";

export const Schema = z.object({
  title: z.string().min(8).max(26).default("API Request / Response").meta({
    description: "Main heading shown at the top-left.",
  }),
  method: z.enum(["GET", "POST", "PATCH", "DELETE"]).default("POST").meta({
    description: "HTTP method badge text.",
  }),
  endpoint: z.string().min(8).max(48).default("/api/v1/users/authenticate").meta({
    description: "Endpoint path text.",
  }),
  headers: z
    .array(z.string().min(12).max(44))
    .min(2)
    .max(2)
    .default(["Content-Type: application/json", "Authorization: Bearer <token>"])
    .meta({
      description: "Two header lines shown in the endpoint card.",
    }),
  requestSnippet: z.object({
    language: z.string().min(2).max(10),
    fileName: z.string().min(3).max(24),
    content: z.string().min(20).max(220),
  }).default({
    language: "json",
    fileName: "request.json",
    content: `{
  "email": "user@example.com user@example.com user@example.com user@example.com user@example.com" ,
  "password": "securepassword123"
}`,
  }).meta({
    description: "Request payload example.",
  }),
  responseSnippet: z.object({
    language: z.string().min(2).max(10),
    fileName: z.string().min(3).max(24),
    content: z.string().min(20).max(620),
  }).default({
    language: "json",
    fileName: "response.json",
    content: `{
  "success": true,
  "user": {
    "id": "usr_1234567890",
    "email": "user@example.com",
    "name": "John Doe",
    "role": "admin"
  },
  "token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "expiresIn": 3600
}`,
  }).meta({
    description: "Response payload example.",
  }),
  pageLabel: z.string().min(3).max(8).default("3 / 11").meta({
    description: "Bottom pagination label.",
  }),
});

export type SchemaType = z.infer<typeof Schema>;

const CodeSlide03ApiRequestResponse = ({
  data,
}: {
  data: Partial<SchemaType>;
}) => {

  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden  bg-[#101B37] p-[53px] ">

      <div className="relative z-10 flex h-full flex-col">
        <h2 className="text-[64px] font-medium  text-[#ffffff]">{data.title}</h2>

        <div className="mt-[22px] grid  flex-1 grid-cols-2 gap-[22px]">
          <div className="flex  flex-col gap-[12px] ">
            <div className="rounded-[14px] border border-[#1D293D80] bg-[#0F172B80] p-[14px]">
              <div className="flex items-center gap-5 pb-[14px] border-b border-[#1D293D80]">
                <p className="rounded-[12px] bg-[#2B7FFF33] px-[23px] py-[10px] text-[14px] uppercase tracking-[0.06em] text-[#51A2FF]">
                  {data.method}
                </p>
                <p className="text-[23px] text-[#dde5ff]">{data.endpoint}</p>
              </div>
              <p className="mt-[21px] text-[18px] uppercase tracking-[0.08em] text-[#90a1d8]">Headers</p>
              <div className="mt-[15px] space-y-[4px] text-[24px] text-[#cbd4f8]">
                {data.headers?.map((item) => (
                  <p key={item} className="text-[18px] text-[#CAD5E2]">{item}</p>
                ))}
              </div>
            </div>

            <div className=" flex-1 bg-[#0F172B80] border border-[#1D293D80]  rounded-[18px] ">
              <p className="text-[18px] text-[#CAD5E2] capitalize bg-[#1D293D80] rounded-t-[18px]  border border-[#1D293D80] p-[14px]">{data.requestSnippet?.fileName}</p>
              <pre className=" w-full px-[14px] py-[20px] text-[#ffffff] whitespace-pre-wrap break-words overflow-hidden">

                <code className="w-full ">
                  {data.requestSnippet?.content}
                </code>
              </pre>
            </div>
          </div>

          <div className=" flex-1 bg-[#0F172B80] border border-[#1D293D80]  rounded-[18px] ">
            <p className="text-[18px] text-[#CAD5E2] capitalize bg-[#1D293D80] rounded-t-[18px]  border border-[#1D293D80] p-[14px]">{data.responseSnippet?.fileName}</p>
            <pre className=" w-full px-[14px] py-[20px] text-[#ffffff] whitespace-pre-wrap break-words overflow-hidden">

              <code className="w-full ">
                {data.responseSnippet?.content}
              </code>
            </pre>
          </div>
        </div>
      </div>

      <div className="absolute bottom-[26px] z-50 left-1/2 -translate-x-1/2 rounded-full border border-[#31415880] bg-[#1D293DCC] px-[22px] py-[8px] text-[14px] text-[#CAD5E2]">
        {data.pageLabel}
      </div>
    </div>
  );
};

export default CodeSlide03ApiRequestResponse;
