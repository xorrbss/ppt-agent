from typing import List, Optional
from pydantic import BaseModel, Field, HttpUrl, EmailStr

from models.presentation_layout import PresentationLayoutModel, SlideLayoutModel
from models.presentation_outline_model import PresentationOutlineModel


class ContactInfoModel(BaseModel):
    email: Optional[EmailStr] = Field(None, description="Contact email")
    phone: Optional[str] = Field(
        None, min_length=5, max_length=50, description="Contact phone number"
    )
    website: Optional[HttpUrl] = Field(None, description="Website URL")


# First Slide Layout
class FirstSlideModel(BaseModel):
    title: str = Field(
        "Welcome to Our Presentation",
        min_length=3,
        max_length=100,
        description="Main title of the presentation",
    )
    subtitle: Optional[str] = Field(
        None, min_length=10, max_length=200, description="Optional subtitle or tagline"
    )
    author: Optional[str] = Field(
        "John Doe",
        min_length=2,
        max_length=100,
        description="Author or presenter name",
    )
    date: Optional[str] = Field(None, description="Presentation date")
    company: Optional[str] = Field(
        "Company Name",
        min_length=2,
        max_length=100,
        description="Company or organization name",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Bullet Point Slide Layout
class BulletPointSlideModel(BaseModel):
    title: str = Field(
        "Key Points",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    icon: Optional[str] = Field(None, description="Icon to display in the slide")
    bulletPoints: List[str] = Field(
        [
            "First key point that highlights important information",
            "Second bullet point with valuable insights",
            "Third point demonstrating clear benefits",
            "Fourth item showcasing key features",
        ],
        min_length=2,
        max_length=8,
        description="List of bullet points (2-8 items)",
    )


# Image Slide Layout
class ImageSlideModel(BaseModel):
    title: str = Field(
        "Image Showcase",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        "Subtitle for the slide",
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    image: HttpUrl = Field(
        "https://images.unsplash.com/photo-1460925895917-afdab827c52f?w=800&h=600&fit=crop",
        description="Main image URL",
    )
    imageCaption: Optional[str] = Field(
        "Image caption",
        min_length=5,
        max_length=200,
        description="Optional image caption or description",
    )
    content: Optional[str] = Field(
        None,
        min_length=10,
        max_length=600,
        description="Optional supporting content text",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Statistics Slide Layout
class StatisticItemModel(BaseModel):
    value: str = Field(
        min_length=1,
        max_length=20,
        description="Statistical value (e.g., '250%', '$1.2M', '99.9%')",
    )
    label: str = Field(
        min_length=3, max_length=100, description="Description of the statistic"
    )
    trend: Optional[str] = Field(
        None, description="Trend direction indicator", pattern="^(up|down|neutral)$"
    )
    context: Optional[str] = Field(
        None,
        min_length=5,
        max_length=200,
        description="Additional context or time period",
    )


class StatisticsSlideModel(BaseModel):
    title: str = Field(
        "Key Statistics",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    statistics: List[StatisticItemModel] = Field(
        [
            StatisticItemModel(
                value="250%",
                label="Revenue Growth",
                trend="up",
                context="Year over year increase",
            ),
            StatisticItemModel(
                value="50M+",
                label="Active Users",
                trend="up",
                context="Global user base",
            ),
            StatisticItemModel(
                value="99.9%",
                label="Uptime",
                trend="neutral",
                context="Service reliability",
            ),
            StatisticItemModel(
                value="24/7",
                label="Support",
                trend="neutral",
                context="Customer service",
            ),
        ],
        min_length=2,
        max_length=6,
        description="List of statistics (2-6 items)",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Quote Slide Layout
class QuoteSlideModel(BaseModel):
    title: str = Field(
        "Testimonials",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    quote: str = Field(
        "This solution has transformed our business operations and exceeded all expectations.",
        min_length=10,
        max_length=500,
        description="The main quote or testimonial",
    )
    author: str = Field(
        "John Smith",
        min_length=2,
        max_length=100,
        description="Quote author name",
    )
    authorTitle: Optional[str] = Field(
        None, min_length=2, max_length=100, description="Author job title or position"
    )
    company: Optional[str] = Field(
        None, min_length=2, max_length=100, description="Author company or organization"
    )
    authorImage: Optional[HttpUrl] = Field(None, description="URL to author photo")
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Timeline Slide Layout
class TimelineItemModel(BaseModel):
    date: str = Field(min_length=2, max_length=50, description="Date or time period")
    title: str = Field(
        min_length=3, max_length=100, description="Event or milestone title"
    )
    description: str = Field(
        min_length=10, max_length=300, description="Event description"
    )
    status: str = Field(
        "upcoming",
        description="Timeline item status",
        pattern="^(completed|current|upcoming)$",
    )


class TimelineSlideModel(BaseModel):
    title: str = Field(
        "Project Timeline",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    timelineItems: List[TimelineItemModel] = Field(
        [
            TimelineItemModel(
                date="Q1 2024",
                title="Project Initiation",
                description="Project planning, team assembly, and initial requirements gathering",
                status="completed",
            ),
            TimelineItemModel(
                date="Q2 2024",
                title="Development Phase",
                description="Core development work, prototype creation, and testing implementation",
                status="current",
            ),
            TimelineItemModel(
                date="Q3 2024",
                title="Testing & QA",
                description="Comprehensive testing, quality assurance, and user acceptance testing",
                status="upcoming",
            ),
            TimelineItemModel(
                date="Q4 2024",
                title="Launch & Deployment",
                description="Final deployment, go-live activities, and post-launch monitoring",
                status="upcoming",
            ),
        ],
        min_length=2,
        max_length=6,
        description="Timeline events (2-6 items)",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Team Slide Layout
class TeamMemberModel(BaseModel):
    name: str = Field(min_length=2, max_length=100, description="Team member name")
    title: str = Field(min_length=2, max_length=100, description="Job title or role")
    image: Optional[HttpUrl] = Field(None, description="URL to team member photo")
    bio: Optional[str] = Field(
        None,
        min_length=10,
        max_length=300,
        description="Brief biography or description",
    )
    email: Optional[EmailStr] = Field(None, description="Contact email")
    linkedin: Optional[HttpUrl] = Field(None, description="LinkedIn profile URL")


class TeamSlideModel(BaseModel):
    title: str = Field(
        "Meet Our Team",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or team description",
    )
    teamMembers: List[TeamMemberModel] = Field(
        [
            TeamMemberModel(
                name="Sarah Johnson",
                title="Chief Executive Officer",
                image="https://images.unsplash.com/photo-1494790108755-2616b612b786?w=300&h=300&fit=crop&crop=face",
                bio="Strategic leader with 15+ years experience driving innovation and growth in technology companies.",
                email="sarah@company.com",
                linkedin="https://linkedin.com/in/sarahjohnson",
            ),
            TeamMemberModel(
                name="Michael Chen",
                title="Chief Technology Officer",
                image="https://images.unsplash.com/photo-1472099645785-5658abf4ff4e?w=300&h=300&fit=crop&crop=face",
                bio="Technology visionary specializing in scalable architecture and emerging technologies.",
                email="michael@company.com",
                linkedin="https://linkedin.com/in/michaelchen",
            ),
            TeamMemberModel(
                name="Emma Rodriguez",
                title="Head of Design",
                image="https://images.unsplash.com/photo-1438761681033-6461ffad8d80?w=300&h=300&fit=crop&crop=face",
                bio="Creative director passionate about user-centered design and innovative digital experiences.",
                email="emma@company.com",
                linkedin="https://linkedin.com/in/emmarodriguez",
            ),
        ],
        min_length=1,
        max_length=6,
        description="Team members (1-6 people)",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Process Slide Layout
class ProcessStepModel(BaseModel):
    step: int = Field(ge=1, le=10, description="Step number")
    title: str = Field(min_length=3, max_length=100, description="Step title")
    description: str = Field(
        min_length=10, max_length=200, description="Step description"
    )


class ProcessSlideModel(BaseModel):
    title: str = Field(
        "Our Process",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    processSteps: List[ProcessStepModel] = Field(
        [
            ProcessStepModel(
                step=1,
                title="Discovery",
                description="Understanding requirements and gathering initial insights",
            ),
            ProcessStepModel(
                step=2,
                title="Planning",
                description="Strategic planning and roadmap development",
            ),
            ProcessStepModel(
                step=3,
                title="Implementation",
                description="Executing the plan with precision and quality",
            ),
            ProcessStepModel(
                step=4,
                title="Delivery",
                description="Final delivery and ongoing support",
            ),
        ],
        min_length=2,
        max_length=6,
        description="Process steps (2-6 items)",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Two Column Slide Layout
class ColumnContentModel(BaseModel):
    title: str = Field(min_length=3, max_length=100, description="Column title")
    content: str = Field(min_length=10, max_length=800, description="Column content")


class TwoColumnSlideModel(BaseModel):
    title: str = Field(
        "Two Column Layout",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    leftColumn: ColumnContentModel = Field(
        ColumnContentModel(
            title="Left Column",
            content="Content for the left column goes here. This can include detailed information, explanations, or supporting details.",
        ),
        description="Left column content",
    )
    rightColumn: ColumnContentModel = Field(
        ColumnContentModel(
            title="Right Column",
            content="Content for the right column goes here. This can include additional information, comparisons, or contrasting details.",
        ),
        description="Right column content",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Conclusion Slide Layout
class ConclusionSlideModel(BaseModel):
    title: str = Field(
        "Conclusion",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    keyTakeaways: List[str] = Field(
        [
            "Successfully achieved our primary objectives",
            "Demonstrated significant value and impact",
            "Established clear next steps for continued success",
            "Built strong foundation for future growth",
        ],
        min_length=2,
        max_length=6,
        description="Key takeaways or summary points (2-6 items)",
    )
    callToAction: Optional[str] = Field(
        None,
        min_length=5,
        max_length=150,
        description="Optional call to action or next steps",
    )
    contactInfo: Optional[ContactInfoModel] = Field(
        None, description="Optional contact information"
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Content Slide Layout
class ContentSlideModel(BaseModel):
    title: str = Field(
        "Slide Title",
        min_length=3,
        max_length=100,
        description="Title of the slide",
    )
    subtitle: Optional[str] = Field(
        None,
        min_length=3,
        max_length=150,
        description="Optional subtitle or description",
    )
    content: str = Field(
        "Your slide content goes here. This is where you can add detailed information, explanations, or any other text content that supports your presentation.",
        min_length=10,
        max_length=1000,
        description="Main content text",
    )
    backgroundImage: Optional[HttpUrl] = Field(
        None, description="URL to background image for the slide"
    )


# Create the presentation layout with all slide types
presentation_layout = PresentationLayoutModel(
    name="Complete Presentation Layout",
    slides=[
        SlideLayoutModel(
            id="first-slide",
            name="First Slide",
            json_schema=FirstSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="bullet-point-slide",
            name="Bullet Point Slide",
            json_schema=BulletPointSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="image-slide",
            name="Image Slide",
            json_schema=ImageSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="statistics-slide",
            name="Statistics Slide",
            json_schema=StatisticsSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="quote-slide",
            name="Quote Slide",
            json_schema=QuoteSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="timeline-slide",
            name="Timeline Slide",
            json_schema=TimelineSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="team-slide",
            name="Team Slide",
            json_schema=TeamSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="process-slide",
            name="Process Slide",
            json_schema=ProcessSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="two-column-slide",
            name="Two Column Slide",
            json_schema=TwoColumnSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="conclusion-slide",
            name="Conclusion Slide",
            json_schema=ConclusionSlideModel.model_json_schema(),
        ),
        SlideLayoutModel(
            id="content-slide",
            name="Content Slide",
            json_schema=ContentSlideModel.model_json_schema(),
        ),
    ],
)

# print(presentation_layout.model_dump_json())

print(PresentationOutlineModel.model_json_schema())
