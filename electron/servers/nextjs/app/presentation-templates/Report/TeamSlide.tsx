import * as z from "zod";


const MemberSchema = z.object({
  title: z.string().min(2).max(24).meta({
    description: "Short role or title shown above the member name.",
  }),
  name: z.string().min(2).max(32).meta({
    description: "Member name shown at the bottom of the card.",
  }),
  image: z.object({
    __image_url__: z.string().default("https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg"),
    __image_prompt__: z.string().default("Professional portrait of a team member"),
  }).default({
    __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
    __image_prompt__: "Professional portrait of a team member",
  }),
});

export const slideLayoutId = "team-slide";
export const slideLayoutName = "Team Slide";
export const slideLayoutDescription =
  "A team slide made of five vertical portrait cards placed side by side from edge to edge. Each card uses a full-height image background with a content overlay at the bottom containing a short title line and a larger name line.";

export const Schema = z.object({
  members: z
    .array(MemberSchema)
    .min(5)
    .max(5)
    .default([
      {
        title: "Title",
        name: "Lanny LA",
        image: {
          __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
          __image_prompt__: "Professional portrait of a male team member",
        },
      },
      {
        title: "Title",
        name: "Lanny LA",
        image: {
          __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
          __image_prompt__: "Professional portrait of a female team member",
        },
      },
      {
        title: "Title",
        name: "Lanny LA",
        image: {
          __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
          __image_prompt__: "Professional portrait of a business manager",
        },
      },
      {
        title: "Title",
        name: "Lanny LA",
        image: {
          __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
          __image_prompt__: "Professional portrait of a senior employee",
        },
      },
      {
        title: "Title",
        name: "Lanny LA",
        image: {
          __image_url__: "https://presenton-public.s3.ap-southeast-1.amazonaws.com/static/images/placeholder.jpg",
          __image_prompt__: "Professional portrait of a young executive",
        },
      },
    ])
    .meta({
      description: "Five team members rendered as portrait cards.",
    }),
});

export type SchemaType = z.infer<typeof Schema>;

const TeamSlide = ({ data }: { data: Partial<SchemaType> }) => {

  return (
    <div className="relative h-[720px] w-[1280px] overflow-hidden rounded-[24px] bg-white">
      <div className="grid h-full "
        style={{ gridTemplateColumns: `repeat(${data?.members?.length}, minmax(0, 1fr))` }}
      >
        {data?.members?.map((member, index) => (
          <div
            key={`${member.name}-${index}`}
            className="relative h-full overflow-hidden"
          >
            <img
              src={member.image.__image_url__}
              alt={member.image.__image_prompt__}
              className="h-full w-full object-cover"
            />
            <div className="absolute inset-x-0 bottom-0 h-[240px] bg-gradient-to-t from-[#4d4ef3] via-[#4d4ef3]/55 to-transparent" />
            <div className="absolute left-0 bottom-0 p-[33px]  text-white">
              <p className="text-[21px] tracking-[2.074px] font-medium text-white/90">{member.title}</p>
              <p className="mt-[14px] text-[28px] ">
                {member.name}
              </p>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
};

export default TeamSlide;
