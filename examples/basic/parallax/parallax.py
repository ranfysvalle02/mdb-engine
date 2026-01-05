#!/usr/bin/env python3
"""
Parallax Engine - GitHub Repository Analysis Tool

The Parallax Engine orchestrates concurrent analysis of GitHub repositories from two focused angles:
1. Relevance: Why this repository/implementation matters given your watchlist - personalized context and urgency
2. Technical: Concise engineering assessment - architecture, patterns, implementation details
"""
import asyncio
import json
import logging
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

# Setup logger first
logger = logging.getLogger("Parallax")

# Try to import requests for GitHub GraphQL API
try:
    import requests

    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    logger.error("requests not available - required for GitHub GraphQL API")

# Try to import yaml for parsing frontmatter
try:
    import yaml

    YAML_AVAILABLE = True
except ImportError:
    YAML_AVAILABLE = False
    logger.warning("yaml not available - YAML frontmatter parsing will be skipped")

# OpenAI imports
try:
    from openai import AzureOpenAI, OpenAI

    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.warning("OpenAI SDK not available")

from schema_generator import create_dynamic_model, get_default_lens_configs
from schemas import ParallaxReport

# The Filter Config - Watchlist for relevant tech keywords
WATCHLIST = ["MongoDB", "agents", "memory"]

# GitHub GraphQL API endpoint
GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


class ParallaxEngine:
    """
    The Parallax Engine analyzes GitHub repositories from Relevance and Technical perspectives.

    Architecture:
    1. Search GitHub repositories by watchlist keywords using GraphQL
    2. Extract AGENTS.md or LLMs.md files from matching repos
    3. Fan-out to two concurrent agents (Relevance, Technical)
    4. Aggregate results into ParallaxReport
    5. Store in MongoDB for dashboard visualization
    """

    def __init__(
        self,
        openai_client,
        db,
        watchlist: Optional[List[str]] = None,
        deployment_name: str = "gpt-4o",
        github_token: Optional[str] = None,
    ):
        """
        Initialize the Parallax Engine.

        Args:
            openai_client: AzureOpenAI or OpenAI client instance
            db: Scoped MongoDB database instance
            watchlist: Optional list of keywords to watch (defaults to WATCHLIST)
            deployment_name: Model deployment name (for Azure) or model name (for OpenAI)
            github_token: GitHub personal access token for GraphQL API (required)
        """
        self.openai_client = openai_client
        self.db = db
        self.deployment_name = deployment_name
        self.watchlist = watchlist or WATCHLIST
        self.scan_limit = 50  # Default scan limit
        self.min_stars = 50  # Default minimum stars
        self.language_filter = None  # Optional language filter (e.g., "python", "javascript")
        # Target files to search for and analyze (exact matches and patterns)
        # Patterns with * will match any file ending with that suffix
        self.target_files = ["AGENTS.md", "CLAUDE.md"]
        self.lens_configs = {}  # Cache for lens configurations
        self.lens_models = {}  # Cache for dynamically generated models
        self.temperature = 0.0  # Strict adherence to facts for Parallax

        # Get GitHub token from parameter or environment
        self.github_token = github_token or os.getenv("GITHUB_TOKEN")
        if not self.github_token:
            raise RuntimeError(
                "GITHUB_TOKEN is required for GitHub GraphQL API. Set it as an environment variable or pass it to ParallaxEngine."
            )

        if not OPENAI_AVAILABLE:
            raise RuntimeError("OpenAI SDK required for Parallax Engine")

        if not REQUESTS_AVAILABLE:
            raise RuntimeError("requests library required for GitHub GraphQL API")

        logger.info(f"Parallax Engine initialized with deployment: {deployment_name}")

    def _search_repos_via_graphql(
        self, keyword: str, min_stars: int = 50, language: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Search GitHub repositories via GraphQL API for a single keyword.

        Args:
            keyword: Keyword to search for
            min_stars: Minimum number of stars
            language: Optional programming language filter (e.g., "python", "javascript")

        Returns:
            List of repositories with AGENTS.md or LLMs.md files
        """
        if not REQUESTS_AVAILABLE:
            logger.error("requests library not available")
            return []

        url = GITHUB_GRAPHQL_URL
        headers = {
            "Authorization": f"Bearer {self.github_token}",
            "Content-Type": "application/json",
        }

        # GraphQL query to search repos by keyword, then fetch target files from file tree
        # This allows us to find ALL files matching our patterns (AGENTS.md, *.mdc, etc.)
        # Also fetches additional repository metadata
        search_query = """
        query($search_query: String!) {
          search(query: $search_query, type: REPOSITORY, first: 20) {
            nodes {
              ... on Repository {
                nameWithOwner
                url
                description
                stargazers {
                  totalCount
                }
                forks {
                  totalCount
                }
                watchers {
                  totalCount
                }
                pullRequests(states: OPEN) {
                  totalCount
                }
                issues(states: OPEN) {
                  totalCount
                }
                updatedAt
                isArchived
                isFork
                primaryLanguage {
                  name
                }
                defaultBranchRef {
                  name
                  target {
                    ... on Commit {
                      tree {
                        entries {
                          name
                          type
                          object {
                            ... on Blob {
                              text
                            }
                          }
                        }
                      }
                      history(first: 1) {
                        nodes {
                          message
                          committedDate
                        }
                      }
                    }
                  }
                }
              }
            }
          }
        }
        """

        # Build search query: Search for repos containing the keyword
        # Focus on finding repos that use the keyword, then we'll check for target files
        search_string = f'"{keyword}" stars:>{min_stars}'
        if language:
            search_string += f" language:{language}"
        logger.info(f"🔍 GraphQL Search Query: '{search_string}'")
        logger.info(f"   Will search for target files: {', '.join(self.target_files)}")

        try:
            response = requests.post(
                url,
                json={"query": search_query, "variables": {"search_query": search_string}},
                headers=headers,
                timeout=30,
            )

            if response.status_code != 200:
                logger.error(f"GraphQL API failed: {response.status_code} - {response.text}")
                return []

            data = response.json()

            # Check for GraphQL errors
            if "errors" in data:
                logger.error(f"GraphQL errors: {data['errors']}")
                return []

            repos = data.get("data", {}).get("search", {}).get("nodes", [])
            valid_findings = []

            for repo in repos:
                name_with_owner = repo["nameWithOwner"]
                stars = repo["stargazers"]["totalCount"]
                description = repo.get("description", "")

                # Get file tree entries from root directory
                files_found = []
                tree = repo.get("defaultBranchRef", {}).get("target", {}).get("tree", {})
                entries = tree.get("entries", []) if tree else []

                # Convert target_files patterns to a searchable list
                # Handle exact matches and patterns like "*.mdc"
                target_patterns = []
                exact_matches = []
                for pattern in self.target_files:
                    if "*" in pattern:
                        # Pattern match (e.g., "*.mdc")
                        target_patterns.append(pattern.replace("*", ""))
                    else:
                        # Exact match (e.g., "AGENTS.md")
                        exact_matches.append(pattern)

                # Search through file tree entries for matching files
                for entry in entries:
                    entry_name = entry.get("name", "")
                    entry_type = entry.get("type", "")

                    # Check if it's a blob (file) and matches our target files
                    if entry_type == "blob":
                        # Check exact matches
                        if entry_name in exact_matches:
                            blob = entry.get("object", {})
                            if blob and blob.get("text"):
                                files_found.append(
                                    {"filename": entry_name, "content": blob["text"]}
                                )
                        # Check pattern matches (e.g., *.mdc)
                        else:
                            for pattern_suffix in target_patterns:
                                if entry_name.endswith(pattern_suffix):
                                    blob = entry.get("object", {})
                                    if blob and blob.get("text"):
                                        files_found.append(
                                            {"filename": entry_name, "content": blob["text"]}
                                        )
                                    break  # Found a match, no need to check other patterns

                # Process ALL files found (not just one)
                if files_found:
                    # Combine all file contents for keyword checking
                    all_content = "\n\n---\n\n".join([f["content"] for f in files_found])
                    all_content_lower = all_content.lower()
                    keyword_lower = keyword.lower()
                    keyword_found = keyword_lower in all_content_lower

                    # Also check repo description
                    desc_lower = description.lower() if description else ""
                    keyword_in_desc = keyword_lower in desc_lower

                    # Log which files were found
                    file_names = ", ".join([f["filename"] for f in files_found])
                    if keyword_found:
                        logger.info(
                            f"   ✅ Found: {name_with_owner} (⭐ {stars}) - Files: {file_names} [keyword '{keyword}' in files]"
                        )
                    elif keyword_in_desc:
                        logger.info(
                            f"   📋 Found: {name_with_owner} (⭐ {stars}) - Files: {file_names} [keyword '{keyword}' in description, analyzing files]"
                        )
                    else:
                        logger.info(
                            f"   📋 Found: {name_with_owner} (⭐ {stars}) - Files: {file_names} [will analyze for relevance]"
                        )

                    # Parse YAML frontmatter from AGENTS.md/LLMs.md if present
                    yaml_data = {}
                    for file_info in files_found:
                        if file_info["filename"] in ["AGENTS.md", "LLMs.md"]:
                            content = file_info["content"]
                            if YAML_AVAILABLE and content.startswith("---"):
                                try:
                                    parts = content.split("---", 2)
                                    if len(parts) >= 3:
                                        parsed_yaml = yaml.safe_load(parts[1]) or {}
                                        yaml_data.update(
                                            parsed_yaml
                                        )  # Merge YAML from multiple files
                                except (yaml.YAMLError, ValueError, TypeError, AttributeError):
                                    # Type 2: Recoverable - YAML parsing failed, skip frontmatter
                                    logger.debug(
                                        f"Failed to parse YAML frontmatter from {file_info['filename']} for {name_with_owner}",
                                        exc_info=True,
                                    )

                    # Split nameWithOwner into owner and name
                    parts = name_with_owner.split("/", 1)
                    owner = parts[0] if len(parts) > 0 else ""
                    name = parts[1] if len(parts) > 1 else name_with_owner

                    # Combine all file contents for analysis (separated by markers)
                    combined_content = "\n\n".join(
                        [f"=== {f['filename']} ===\n{f['content']}" for f in files_found]
                    )

                    # Extract additional metadata
                    forks_count = repo.get("forks", {}).get("totalCount", 0)
                    watchers_count = repo.get("watchers", {}).get("totalCount", 0)
                    pull_requests_count = repo.get("pullRequests", {}).get("totalCount", 0)
                    issues_count = repo.get("issues", {}).get("totalCount", 0)
                    updated_at = repo.get("updatedAt", "")
                    is_archived = repo.get("isArchived", False)
                    is_fork = repo.get("isFork", False)
                    primary_language = (
                        repo.get("primaryLanguage", {}).get("name")
                        if repo.get("primaryLanguage")
                        else None
                    )

                    # Get last commit info from defaultBranchRef target history
                    last_commit_message = None
                    last_commit_date = None
                    default_branch_ref = repo.get("defaultBranchRef", {})
                    if default_branch_ref:
                        target = default_branch_ref.get("target", {})
                        if target:
                            history = target.get("history", {})
                            if history:
                                nodes = history.get("nodes", [])
                                if nodes and len(nodes) > 0:
                                    last_commit = nodes[0]
                                    last_commit_message = last_commit.get("message", "").strip()
                                    last_commit_date = last_commit.get("committedDate", "")

                    # Create entry with all files and metadata
                    valid_findings.append(
                        {
                            "repo_id": name_with_owner,
                            "repo_name": name,
                            "repo_owner": owner,
                            "stars": stars,
                            "url": repo["url"],
                            "description": description,
                            "files_found": [
                                f["filename"] for f in files_found
                            ],  # List of all files found
                            "file_found": files_found[0][
                                "filename"
                            ],  # Primary file (for backward compatibility)
                            "full_content": combined_content,  # Combined content from all files
                            "files": {
                                f["filename"]: f["content"] for f in files_found
                            },  # Individual file contents
                            "yaml_frontmatter": yaml_data,
                            "matched_keywords": [keyword],
                            "keyword_in_file": keyword_found,
                            "keyword_in_description": keyword_in_desc,
                            # Additional metadata
                            "pull_requests_count": pull_requests_count,
                            "issues_count": issues_count,
                            "last_updated": updated_at,
                            "last_commit_message": last_commit_message,
                            "last_commit_date": last_commit_date,
                            "forks_count": forks_count,
                            "watchers_count": watchers_count,
                            "is_archived": is_archived,
                            "is_fork": is_fork,
                            "primary_language": primary_language,
                        }
                    )
                else:
                    logger.debug(
                        f"   ⚠️  Skipping {name_with_owner}: No README.md, AGENTS.md, or LLMs.md found"
                    )

            logger.info(
                f"   📊 Found {len(valid_findings)} repositories with target files (analyzing all files found)"
            )
            return valid_findings

        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError):
            # Type 2: Recoverable - GitHub search failed, return empty list
            logger.error(f"Error searching GitHub for keyword '{keyword}'", exc_info=True)
            return []

    async def _search_and_filter_repos(self) -> List[Dict[str, Any]]:
        """
        Search GitHub repositories for all watchlist keywords and aggregate results.

        Returns:
            List of repositories with AGENTS.md or LLMs.md files, deduplicated
        """
        if not self.watchlist:
            logger.warning("Watchlist is empty, nothing to search")
            return []

        logger.info(f"Searching GitHub for {len(self.watchlist)} keywords: {self.watchlist}")

        # Search for each keyword (run in parallel using asyncio)
        all_findings = []
        seen_repos = set()  # For deduplication

        # Run searches in parallel
        loop = asyncio.get_event_loop()
        tasks = [
            loop.run_in_executor(
                None, self._search_repos_via_graphql, keyword, self.min_stars, self.language_filter
            )
            for keyword in self.watchlist
        ]
        results = await asyncio.gather(*tasks)

        # Aggregate and deduplicate results
        for keyword_results in results:
            for repo in keyword_results:
                repo_id = repo["repo_id"]
                if repo_id not in seen_repos:
                    seen_repos.add(repo_id)
                    all_findings.append(repo)
                else:
                    # Repo already seen, merge matched_keywords
                    existing = next((r for r in all_findings if r["repo_id"] == repo_id), None)
                    if existing:
                        existing["matched_keywords"].extend(repo["matched_keywords"])
                        # Deduplicate keywords
                        existing["matched_keywords"] = list(set(existing["matched_keywords"]))

        # Sort by stars (descending)
        all_findings.sort(key=lambda x: x["stars"], reverse=True)

        logger.info(
            f"Found {len(all_findings)} unique repositories with AGENTS.md or LLMs.md files"
        )
        return all_findings

    async def _load_lens_config(self, lens_name: str) -> Optional[Dict[str, Any]]:
        """Load lens configuration from database"""
        try:
            config = await self.db.lens_configs.find_one({"lens_name": lens_name})
            if config:
                # Remove MongoDB _id for easier handling
                config.pop("_id", None)
                self.lens_configs[lens_name] = config
                return config
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError):
            # Type 2: Recoverable - lens config load failed, return None
            logger.debug(f"Could not load lens config for {lens_name}", exc_info=True)
        return None

    async def _get_lens_model(self, lens_name: str) -> Optional[Any]:
        """Get or create the dynamic Pydantic model for a lens"""
        # Check cache first
        if lens_name in self.lens_models:
            return self.lens_models[lens_name]

        # Load config
        config = await self._load_lens_config(lens_name)
        if not config:
            logger.error(f"No configuration found for lens: {lens_name}")
            return None

        # Generate model from schema fields
        try:
            model = create_dynamic_model(f"{lens_name}View", config.get("schema_fields", []))
            self.lens_models[lens_name] = model
            return model
        except (AttributeError, RuntimeError, ValueError, TypeError, KeyError):
            # Type 2: Recoverable - model creation failed, return None
            logger.error(f"Failed to create model for {lens_name}", exc_info=True)
            return None

    async def _generate_viewpoint(
        self,
        repo_name: str,
        repo_owner: str,
        url: str,
        file_content: str,
        files_found: str,  # Comma-separated list of files found (e.g., "AGENTS.md, README.md")
        lens_name: str,
        watchlist: Optional[List[str]] = None,
    ) -> Optional[Any]:
        """
        The Agent Worker.
        Takes a lens name, loads its config, and returns a filled-out form.

        Args:
            repo_name: The repository name
            repo_owner: The repository owner
            url: The repository URL
            file_content: The combined content from all files found (README.md, AGENTS.md, LLMs.md)
            files_found: Comma-separated list of files found (e.g., "AGENTS.md, README.md")
            lens_name: Lens name (Technical, Relevance)
            watchlist: Optional list of watchlist keywords (for Relevance lens)

        Returns:
            Parsed schema instance or None on error
        """
        if not self.openai_client:
            logger.error("OpenAI client not initialized")
            return None

        # Load lens configuration
        config = await self._load_lens_config(lens_name)
        if not config:
            logger.error(f"No configuration found for lens: {lens_name}")
            return None

        # Get or create the dynamic model
        schema = await self._get_lens_model(lens_name)
        if not schema:
            logger.error(f"Failed to get model for lens: {lens_name}")
            return None

        try:
            # Use configurable prompt template
            prompt_template = config.get(
                "prompt_template", "You are the {role} Specialist.\n\n{format_instructions}"
            )

            # Format the prompt template with watchlist if provided (for Relevance lens)
            watchlist_str = ", ".join(watchlist) if watchlist else ""

            # Get format instructions from Pydantic schema
            format_instructions = schema.model_json_schema()
            format_instructions_str = (
                f"Respond with valid JSON matching this schema: {format_instructions}"
            )

            system_prompt = prompt_template.format(
                role=lens_name, watchlist=watchlist_str, format_instructions=format_instructions_str
            )

            # Truncate file content if too long (keep first 12000 chars for context since we have multiple files)
            content_preview = file_content[:12000] + ("..." if len(file_content) > 12000 else "")

            user_prompt = f"REPOSITORY: {repo_owner}/{repo_name}\nURL: {url}\nFILES ANALYZED: {files_found}\n\nCONTENT FROM ALL FILES:\n{content_preview}"

            # Call OpenAI API directly
            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            # Use asyncio.to_thread for async execution
            response = await asyncio.to_thread(
                self.openai_client.chat.completions.create,
                model=self.deployment_name,
                messages=messages,
                temperature=self.temperature,
                response_format={"type": "json_object"},  # Request JSON response
            )

            content = response.choices[0].message.content

            # Parse JSON response into Pydantic model
            result_data = json.loads(content)
            result = schema.model_validate(result_data)

            logger.debug(
                f"{lens_name} Viewpoint generated successfully for {repo_owner}/{repo_name}"
            )
            return result

        except json.JSONDecodeError as e:
            logger.error(
                f"{lens_name} Viewpoint Failed: Invalid JSON response - {e}", exc_info=True
            )
            if "content" in locals():
                logger.error(f"Response content: {content[:500]}")
            return None
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError):
            # Type 2: Recoverable - viewpoint generation failed, return None
            logger.error(f"{lens_name} Viewpoint Failed", exc_info=True)
            if "content" in locals():
                logger.error(f"Response content: {content[:500]}")
            return None  # Fail gracefully

    async def _load_watchlist_config(self):
        """Load watchlist configuration from database"""
        try:
            config = await self.db.watchlist_config.find_one({"config_type": "watchlist"})
            if config:
                if config.get("keywords"):
                    self.watchlist = config["keywords"]
                    logger.info(f"Loaded watchlist from config: {self.watchlist}")
                if config.get("scan_limit"):
                    self.scan_limit = config["scan_limit"]
                    logger.info(f"Loaded scan limit from config: {self.scan_limit}")
                if config.get("min_stars"):
                    self.min_stars = config["min_stars"]
                    logger.info(f"Loaded min_stars from config: {self.min_stars}")
                if config.get("language_filter"):
                    self.language_filter = config["language_filter"]
                    logger.info(f"Loaded language_filter from config: {self.language_filter}")
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError, KeyError):
            # Type 2: Recoverable - watchlist config load failed, use defaults
            logger.debug("Could not load watchlist config, using default", exc_info=True)

    async def update_watchlist(
        self,
        keywords: List[str],
        scan_limit: Optional[int] = None,
        min_stars: Optional[int] = None,
        language_filter: Optional[str] = None,
    ) -> bool:
        """Update the watchlist configuration"""
        try:
            update_data = {"keywords": keywords, "updated_at": datetime.utcnow()}
            if scan_limit is not None:
                update_data["scan_limit"] = scan_limit
                self.scan_limit = scan_limit
            if min_stars is not None:
                update_data["min_stars"] = min_stars
                self.min_stars = min_stars
            if language_filter is not None:
                update_data["language_filter"] = language_filter if language_filter else None
                self.language_filter = language_filter if language_filter else None

            await self.db.watchlist_config.update_one(
                {"config_type": "watchlist"}, {"$set": update_data}, upsert=True
            )
            self.watchlist = keywords
            logger.info(
                f"Updated watchlist: {keywords}, scan_limit: {self.scan_limit}, min_stars: {self.min_stars}, language_filter: {self.language_filter}"
            )
            return True
        except (AttributeError, RuntimeError, ConnectionError, ValueError, TypeError):
            # Type 2: Recoverable - watchlist update failed, return False
            logger.error("Failed to update watchlist", exc_info=True)
            return False

    async def get_scan_limit(self) -> int:
        """Get current scan limit"""
        await self._load_watchlist_config()
        return self.scan_limit

    async def get_watchlist(self) -> List[str]:
        """Get current watchlist"""
        await self._load_watchlist_config()
        return self.watchlist

    async def analyze_repositories(self, progress_callback=None) -> List[ParallaxReport]:
        """
        The Main Loop:
        1. Search GitHub repositories (filtered by watchlist keywords)
        2. Extract AGENTS.md or LLMs.md files
        3. Fan-Out to 2 Agents (Relevance, Technical)
        4. Aggregate & Store

        Returns:
            List of ParallaxReport instances
        """
        # Load watchlist and scan limit from DB config if available
        await self._load_watchlist_config()

        logger.info(
            f"Starting analysis: searching GitHub for keywords: {self.watchlist} (min_stars: {self.min_stars})"
        )
        repos = await self._search_and_filter_repos()
        logger.info(f"Found {len(repos)} repositories with AGENTS.md or LLMs.md files")
        reports = []

        new_repos = 0
        cached_repos = 0

        for repo in repos:
            repo_id = repo.get("repo_id")
            if not repo_id:
                continue

            # Check DB cache to save money
            cached = await self.db.parallax_reports.find_one({"repo_id": repo_id})
            if cached:
                cached_repos += 1
                logger.debug(f"Skipping cached repo: {repo_id}")
                continue

            new_repos += 1

            repo_name = repo.get("repo_name", "Unknown")
            repo_owner = repo.get("repo_owner", "Unknown")
            url = repo.get("url", "")
            file_content = repo.get("full_content", "")  # Combined content from all files
            files_found = repo.get("files_found", [])  # List of all files found
            file_found = repo.get(
                "file_found", files_found[0] if files_found else "README.md"
            )  # Primary file (for backward compatibility)
            matched_keywords = repo.get("matched_keywords", [])
            stars = repo.get("stars", 0)

            # Extract metadata
            pull_requests_count = repo.get("pull_requests_count")
            issues_count = repo.get("issues_count")
            last_updated = repo.get("last_updated")
            last_commit_message = repo.get("last_commit_message")
            last_commit_date = repo.get("last_commit_date")
            forks_count = repo.get("forks_count")
            watchers_count = repo.get("watchers_count")
            is_archived = repo.get("is_archived")
            is_fork = repo.get("is_fork")
            primary_language = repo.get("primary_language")

            files_str = ", ".join(files_found) if files_found else file_found
            logger.info(
                f"Orchestrating Parallax View for: {repo_id} (⭐ {stars}) - Analyzing files: {files_str}"
            )

            # --- THE FAN OUT ---
            # Launch 2 focused agents simultaneously
            # Pass combined content from ALL files for analysis
            task_relevance = self._generate_viewpoint(
                repo_name,
                repo_owner,
                url,
                file_content,
                files_str,  # Pass files list as string
                "Relevance",
                watchlist=self.watchlist,
            )
            task_technical = self._generate_viewpoint(
                repo_name, repo_owner, url, file_content, files_str, "Technical"
            )

            # Wait for both views
            r_view, t_view = await asyncio.gather(task_relevance, task_technical)

            # Convert Pydantic models to dicts for storage (support both v1 and v2)
            if r_view:
                if hasattr(r_view, "model_dump"):
                    relevance_dict = r_view.model_dump()
                elif hasattr(r_view, "dict"):
                    relevance_dict = r_view.dict()
                else:
                    relevance_dict = r_view if isinstance(r_view, dict) else {}
            else:
                logger.warning(f"Relevance viewpoint returned None for {repo_id}")
                relevance_dict = {}

            if t_view:
                if hasattr(t_view, "model_dump"):
                    technical_dict = t_view.model_dump()
                elif hasattr(t_view, "dict"):
                    technical_dict = t_view.dict()
                else:
                    technical_dict = t_view if isinstance(t_view, dict) else {}
            else:
                logger.warning(f"Technical viewpoint returned None for {repo_id}")
                technical_dict = {}

            # Only create report if we have at least one valid viewpoint
            if relevance_dict or technical_dict:
                report = ParallaxReport(
                    repo_id=repo_id,
                    repo_name=repo_name,
                    repo_owner=repo_owner,
                    stars=stars,
                    file_found=file_found,  # Primary file (for backward compatibility)
                    original_title=repo_name,  # Use repo name as title
                    url=url,
                    marketing={},  # Empty for backward compatibility
                    sales={},  # Empty for backward compatibility
                    product=technical_dict,  # Keep 'product' key for backward compatibility
                    relevance=relevance_dict if relevance_dict else None,
                    timestamp=datetime.utcnow().isoformat(),
                    matched_keywords=matched_keywords,
                    # Additional metadata
                    pull_requests_count=pull_requests_count,
                    issues_count=issues_count,
                    last_updated=last_updated,
                    last_commit_message=last_commit_message,
                    last_commit_date=last_commit_date,
                    forks_count=forks_count,
                    watchers_count=watchers_count,
                    is_archived=is_archived,
                    is_fork=is_fork,
                    primary_language=primary_language,
                )

                # Store files_found in the report dict for storage
                report_dict = (
                    report.model_dump() if hasattr(report, "model_dump") else report.dict()
                )
                report_dict["files_found"] = files_found  # Store list of all files analyzed

                # Store in MongoDB (support both Pydantic v1 and v2)
                if hasattr(report, "model_dump"):
                    report_dict = report.model_dump()
                elif hasattr(report, "dict"):
                    report_dict = report.dict()
                else:
                    report_dict = report if isinstance(report, dict) else {}
                await self.db.parallax_reports.insert_one(report_dict)
                reports.append(report)
                logger.info(
                    f"Parallax report stored for: {repo_id} (relevance: {bool(relevance_dict)}, technical: {bool(technical_dict)})"
                )

                # Send progress update via callback if provided (for WebSocket streaming)
                if progress_callback:
                    try:
                        await progress_callback(
                            {
                                "type": "report_complete",
                                "repo_id": repo_id,  # Include repo_id for fetching the report
                                "repo_name": repo_name,
                                "stars": stars,
                                "files_analyzed": files_str,
                                "total_processed": new_repos,
                                "total_found": len(repos),
                            }
                        )
                    except (RuntimeError, ConnectionError, OSError, AttributeError):
                        # Type 2: Recoverable - progress callback failed, continue
                        logger.debug("Error sending progress callback", exc_info=True)
            else:
                logger.warning(
                    f"Failed to generate any Parallax view for: {repo_id} (relevance: {r_view is not None}, technical: {t_view is not None})"
                )

        logger.info(
            f"Analysis complete: {new_repos} new repos analyzed, {cached_repos} cached, {len(reports)} reports generated"
        )

        # Send completion message via callback if provided
        if progress_callback:
            try:
                await progress_callback(
                    {
                        "type": "analysis_complete",
                        "total_reports": len(reports),
                        "new_repos": new_repos,
                        "cached_repos": cached_repos,
                    }
                )
            except (RuntimeError, ConnectionError, OSError, AttributeError):
                # Type 2: Recoverable - completion callback failed, continue
                logger.debug("Error sending completion callback", exc_info=True)

        return reports

    # Alias for backward compatibility
    async def analyze_feed(self, progress_callback=None) -> List[ParallaxReport]:
        """Alias for analyze_repositories() for backward compatibility"""
        return await self.analyze_repositories(progress_callback=progress_callback)
