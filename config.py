import os
from dotenv import load_dotenv

load_dotenv()

class Config:
    ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
    CLAUDE_MODEL = "claude-sonnet-4-6"
    DB_PATH = os.path.join(os.path.dirname(__file__), "data", "citizenship.db")
    QUESTIONS_PATH = os.path.join(os.path.dirname(__file__), "data", "questions.json")
    MAX_TOKENS_EVALUATION = 1500
    SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret-change-in-prod")

    EVALUATION_SYSTEM_PROMPT = """You are an experienced GCSE Citizenship examiner marking student revision answers. \
Your role is to award marks generously and fairly, exactly as a real GCSE examiner would.

CORE MARKING PRINCIPLES:
- Award marks for correct knowledge and understanding, regardless of how it is phrased
- Do NOT require students to use the exact words from the mark scheme — accept any answer that conveys the correct meaning
- Do NOT penalise for poor spelling, grammar, or informal language — focus solely on the knowledge demonstrated
- When in doubt whether a student has addressed a point, award the mark — err on the side of generosity
- Award partial credit wherever correct knowledge is shown, even if the full point is not developed
- Never invent mark scheme points, but do award credit for correct knowledge that aligns with provided indicative points
- Be encouraging and constructive — students are revising, not sitting a final exam"""

    TOPICS = {
        "life_in_modern_britain": {
            "label": "Life in Modern Britain",
            "subtopics": {
                "british_values_and_identity": "British Values & Identity",
                "human_rights": "Human Rights",
                "equality_and_discrimination": "Equality & Discrimination",
                "the_law_and_citizens": "The Law & Citizens",
            }
        },
        "rights_and_responsibilities": {
            "label": "Rights & Responsibilities",
            "subtopics": {
                "citizens_rights": "Citizens' Rights",
                "responsibilities_and_duties": "Responsibilities & Duties",
                "consumer_rights": "Consumer Rights",
                "justice_system": "Justice System",
            }
        },
        "government_and_democracy": {
            "label": "Government & Democracy",
            "subtopics": {
                "uk_constitution": "UK Constitution",
                "parliament_and_government": "Parliament & Government",
                "electoral_systems": "Electoral Systems",
                "local_government": "Local Government",
                "political_parties": "Political Parties",
            }
        },
        "uk_and_wider_world": {
            "label": "UK & the Wider World",
            "subtopics": {
                "international_organisations": "International Organisations",
                "global_issues": "Global Issues",
                "uk_foreign_policy": "UK Foreign Policy",
                "trade_and_economics": "Trade & Economics",
            }
        },
        "active_citizenship": {
            "label": "Active Citizenship",
            "subtopics": {
                "campaigning_and_advocacy": "Campaigning & Advocacy",
                "community_action": "Community Action",
                "media_and_democracy": "Media & Democracy",
                "taking_action_projects": "Taking Action Projects",
            }
        },
    }

    TOPIC_COLORS = {
        "life_in_modern_britain": "#4f86c6",
        "rights_and_responsibilities": "#6abf69",
        "government_and_democracy": "#e8934a",
        "uk_and_wider_world": "#9b59b6",
        "active_citizenship": "#e74c3c",
    }
