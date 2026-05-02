--
-- PostgreSQL database dump
--

-- Dumped from database version 14.22
-- Schema-only dump, sanitized for distribution

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: vector; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;


--
-- Name: conversation_history; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_history (
    id integer NOT NULL,
    session_key text NOT NULL,
    agent_id text DEFAULT 'main'::text NOT NULL,
    role text NOT NULL,
    content text NOT NULL,
    embedding public.vector(1536),
    tokens_used integer DEFAULT 0,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: conversation_history_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversation_history_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversation_history_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversation_history_id_seq OWNED BY public.conversation_history.id;


--
-- Name: conversation_transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.conversation_transcripts (
    id integer NOT NULL,
    session_key text NOT NULL,
    session_id text NOT NULL,
    agent_id text DEFAULT 'main'::text,
    channel text DEFAULT 'unknown'::text,
    user_id text,
    started_at timestamp with time zone,
    archived_at timestamp with time zone DEFAULT now(),
    token_count integer DEFAULT 0,
    message_count integer DEFAULT 0,
    transcript text NOT NULL,
    metadata jsonb DEFAULT '{}'::jsonb
);


--
-- Name: conversation_transcripts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.conversation_transcripts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: conversation_transcripts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.conversation_transcripts_id_seq OWNED BY public.conversation_transcripts.id;


--
-- Name: dm_contact_profiles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dm_contact_profiles (
    contact_id character varying(64) NOT NULL,
    contact_name character varying(256),
    instagram_username character varying(100),
    bio text,
    category character varying(50),
    qualification_stage character varying(30) DEFAULT 'rapport'::character varying,
    notes text,
    first_contact_at timestamp with time zone DEFAULT now(),
    last_contact_at timestamp with time zone DEFAULT now(),
    messages_count integer DEFAULT 0,
    is_qualified boolean DEFAULT false,
    agent_id integer DEFAULT 2
);


--
-- Name: dm_conversations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.dm_conversations (
    id integer NOT NULL,
    contact_id character varying(64) NOT NULL,
    contact_name character varying(256),
    direction character varying(10) NOT NULL,
    message text NOT NULL,
    category character varying(50),
    qualification_stage character varying(30),
    created_at timestamp with time zone DEFAULT now(),
    agent_id integer DEFAULT 2
);


--
-- Name: dm_conversations_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.dm_conversations_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: dm_conversations_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.dm_conversations_id_seq OWNED BY public.dm_conversations.id;


--
-- Name: memory_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_chunks (
    id integer NOT NULL,
    source_file text NOT NULL,
    agent_id text DEFAULT 'main'::text NOT NULL,
    chunk_index integer DEFAULT 0 NOT NULL,
    content text NOT NULL,
    embedding public.vector(1536),
    file_hash text,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: memory_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.memory_chunks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: memory_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.memory_chunks_id_seq OWNED BY public.memory_chunks.id;


--
-- Name: memory_facts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.memory_facts (
    id integer NOT NULL,
    agent_id text DEFAULT 'main'::text NOT NULL,
    category text DEFAULT 'general'::text NOT NULL,
    fact text NOT NULL,
    source_session text,
    embedding public.vector(1536),
    relevance_score double precision DEFAULT 1.0,
    created_at timestamp with time zone DEFAULT now(),
    expires_at timestamp with time zone
);


--
-- Name: memory_facts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.memory_facts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: memory_facts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.memory_facts_id_seq OWNED BY public.memory_facts.id;


--
-- Name: sdr_agent_files; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sdr_agent_files (
    id integer NOT NULL,
    agent_id integer,
    filename character varying(255) NOT NULL,
    file_type character varying(10) NOT NULL,
    content text NOT NULL,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: sdr_agent_files_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sdr_agent_files_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sdr_agent_files_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sdr_agent_files_id_seq OWNED BY public.sdr_agent_files.id;


--
-- Name: sdr_agent_sales; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sdr_agent_sales (
    id integer NOT NULL,
    agent_id integer,
    platform character varying(50),
    product character varying(255),
    amount numeric(10,2) DEFAULT 0,
    buyer_name character varying(255),
    buyer_email character varying(255),
    transaction_id character varying(255),
    status character varying(20) DEFAULT 'approved'::character varying,
    created_at timestamp with time zone DEFAULT now(),
    buyer_phone character varying(50)
);


--
-- Name: sdr_agent_sales_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sdr_agent_sales_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sdr_agent_sales_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sdr_agent_sales_id_seq OWNED BY public.sdr_agent_sales.id;


--
-- Name: sdr_agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sdr_agents (
    id integer NOT NULL,
    name character varying(100) NOT NULL,
    company character varying(200),
    status character varying(20) DEFAULT 'inactive'::character varying,
    port integer,
    webhook_url text,
    personality text,
    products text,
    links text,
    blocked_names text,
    spin_flow text,
    calendar_id character varying(100),
    ghl_api_key character varying(200),
    ghl_location_id character varying(100),
    pid integer,
    created_at timestamp with time zone DEFAULT now(),
    updated_at timestamp with time zone DEFAULT now(),
    messages_in integer DEFAULT 0,
    messages_out integer DEFAULT 0,
    contacts integer DEFAULT 0,
    receive_method character varying(20) DEFAULT 'webhook'::character varying,
    send_method character varying(20) DEFAULT 'api'::character varying,
    send_webhook_url text,
    receive_api_endpoint text,
    sales_webhook_secret character varying(100),
    avatar_url text
);


--
-- Name: sdr_agents_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sdr_agents_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sdr_agents_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sdr_agents_id_seq OWNED BY public.sdr_agents.id;


--
-- Name: sdr_cart_abandonments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sdr_cart_abandonments (
    id integer NOT NULL,
    agent_id integer,
    platform character varying(50),
    product character varying(255),
    buyer_name character varying(255),
    buyer_email character varying(255),
    buyer_phone character varying(50),
    event_type character varying(50),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: sdr_cart_abandonments_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sdr_cart_abandonments_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sdr_cart_abandonments_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sdr_cart_abandonments_id_seq OWNED BY public.sdr_cart_abandonments.id;


--
-- Name: sdr_channels; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sdr_channels (
    id integer NOT NULL,
    agent_id integer,
    channel_type character varying(50) NOT NULL,
    webhook_url text,
    status character varying(20) DEFAULT 'active'::character varying,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: sdr_channels_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sdr_channels_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sdr_channels_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sdr_channels_id_seq OWNED BY public.sdr_channels.id;


--
-- Name: session_checkpoints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.session_checkpoints (
    session_id text NOT NULL,
    agent_id text,
    last_message_index integer DEFAULT '-1'::integer,
    last_char_count integer DEFAULT 0,
    updated_at timestamp with time zone DEFAULT now()
);


--
-- Name: session_transcripts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.session_transcripts (
    id integer NOT NULL,
    session_id text NOT NULL,
    agent_id text,
    role text NOT NULL,
    content text NOT NULL,
    message_index integer,
    archived_at timestamp with time zone DEFAULT now()
);


--
-- Name: session_transcripts_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.session_transcripts_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: session_transcripts_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.session_transcripts_id_seq OWNED BY public.session_transcripts.id;


--
-- Name: site_analytics; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.site_analytics (
    id integer NOT NULL,
    site character varying(100) NOT NULL,
    path character varying(200) DEFAULT '/'::character varying,
    ip character varying(50),
    user_agent text,
    referrer text,
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: site_analytics_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.site_analytics_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: site_analytics_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.site_analytics_id_seq OWNED BY public.site_analytics.id;


--
-- Name: sync_status; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sync_status (
    id integer NOT NULL,
    file_path text NOT NULL,
    agent_id text DEFAULT 'main'::text NOT NULL,
    file_hash text NOT NULL,
    chunks_count integer DEFAULT 0,
    last_synced timestamp with time zone DEFAULT now()
);


--
-- Name: sync_status_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.sync_status_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: sync_status_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.sync_status_id_seq OWNED BY public.sync_status.id;


--
-- Name: transcript_chunks; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transcript_chunks (
    id integer NOT NULL,
    transcript_id integer,
    chunk_index integer NOT NULL,
    content text NOT NULL,
    role text DEFAULT 'mixed'::text,
    token_estimate integer DEFAULT 0,
    embedding public.vector(1536),
    created_at timestamp with time zone DEFAULT now()
);


--
-- Name: transcript_chunks_id_seq; Type: SEQUENCE; Schema: public; Owner: -
--

CREATE SEQUENCE public.transcript_chunks_id_seq
    AS integer
    START WITH 1
    INCREMENT BY 1
    NO MINVALUE
    NO MAXVALUE
    CACHE 1;


--
-- Name: transcript_chunks_id_seq; Type: SEQUENCE OWNED BY; Schema: public; Owner: -
--

ALTER SEQUENCE public.transcript_chunks_id_seq OWNED BY public.transcript_chunks.id;


--
-- Name: conversation_history id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_history ALTER COLUMN id SET DEFAULT nextval('public.conversation_history_id_seq'::regclass);


--
-- Name: conversation_transcripts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_transcripts ALTER COLUMN id SET DEFAULT nextval('public.conversation_transcripts_id_seq'::regclass);


--
-- Name: dm_conversations id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dm_conversations ALTER COLUMN id SET DEFAULT nextval('public.dm_conversations_id_seq'::regclass);


--
-- Name: memory_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_chunks ALTER COLUMN id SET DEFAULT nextval('public.memory_chunks_id_seq'::regclass);


--
-- Name: memory_facts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_facts ALTER COLUMN id SET DEFAULT nextval('public.memory_facts_id_seq'::regclass);


--
-- Name: sdr_agent_files id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agent_files ALTER COLUMN id SET DEFAULT nextval('public.sdr_agent_files_id_seq'::regclass);


--
-- Name: sdr_agent_sales id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agent_sales ALTER COLUMN id SET DEFAULT nextval('public.sdr_agent_sales_id_seq'::regclass);


--
-- Name: sdr_agents id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agents ALTER COLUMN id SET DEFAULT nextval('public.sdr_agents_id_seq'::regclass);


--
-- Name: sdr_cart_abandonments id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_cart_abandonments ALTER COLUMN id SET DEFAULT nextval('public.sdr_cart_abandonments_id_seq'::regclass);


--
-- Name: sdr_channels id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_channels ALTER COLUMN id SET DEFAULT nextval('public.sdr_channels_id_seq'::regclass);


--
-- Name: session_transcripts id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_transcripts ALTER COLUMN id SET DEFAULT nextval('public.session_transcripts_id_seq'::regclass);


--
-- Name: site_analytics id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.site_analytics ALTER COLUMN id SET DEFAULT nextval('public.site_analytics_id_seq'::regclass);


--
-- Name: sync_status id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_status ALTER COLUMN id SET DEFAULT nextval('public.sync_status_id_seq'::regclass);


--
-- Name: transcript_chunks id; Type: DEFAULT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcript_chunks ALTER COLUMN id SET DEFAULT nextval('public.transcript_chunks_id_seq'::regclass);


--
-- Name: conversation_history conversation_history_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_history
    ADD CONSTRAINT conversation_history_pkey PRIMARY KEY (id);


--
-- Name: conversation_transcripts conversation_transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.conversation_transcripts
    ADD CONSTRAINT conversation_transcripts_pkey PRIMARY KEY (id);


--
-- Name: dm_contact_profiles dm_contact_profiles_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dm_contact_profiles
    ADD CONSTRAINT dm_contact_profiles_pkey PRIMARY KEY (contact_id);


--
-- Name: dm_conversations dm_conversations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.dm_conversations
    ADD CONSTRAINT dm_conversations_pkey PRIMARY KEY (id);


--
-- Name: memory_chunks memory_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_chunks
    ADD CONSTRAINT memory_chunks_pkey PRIMARY KEY (id);


--
-- Name: memory_chunks memory_chunks_source_file_agent_id_chunk_index_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_chunks
    ADD CONSTRAINT memory_chunks_source_file_agent_id_chunk_index_key UNIQUE (source_file, agent_id, chunk_index);


--
-- Name: memory_facts memory_facts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.memory_facts
    ADD CONSTRAINT memory_facts_pkey PRIMARY KEY (id);


--
-- Name: sdr_agent_files sdr_agent_files_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agent_files
    ADD CONSTRAINT sdr_agent_files_pkey PRIMARY KEY (id);


--
-- Name: sdr_agent_sales sdr_agent_sales_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agent_sales
    ADD CONSTRAINT sdr_agent_sales_pkey PRIMARY KEY (id);


--
-- Name: sdr_agents sdr_agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agents
    ADD CONSTRAINT sdr_agents_pkey PRIMARY KEY (id);


--
-- Name: sdr_agents sdr_agents_port_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agents
    ADD CONSTRAINT sdr_agents_port_key UNIQUE (port);


--
-- Name: sdr_cart_abandonments sdr_cart_abandonments_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_cart_abandonments
    ADD CONSTRAINT sdr_cart_abandonments_pkey PRIMARY KEY (id);


--
-- Name: sdr_channels sdr_channels_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_channels
    ADD CONSTRAINT sdr_channels_pkey PRIMARY KEY (id);


--
-- Name: session_checkpoints session_checkpoints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_checkpoints
    ADD CONSTRAINT session_checkpoints_pkey PRIMARY KEY (session_id);


--
-- Name: session_transcripts session_transcripts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.session_transcripts
    ADD CONSTRAINT session_transcripts_pkey PRIMARY KEY (id);


--
-- Name: site_analytics site_analytics_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.site_analytics
    ADD CONSTRAINT site_analytics_pkey PRIMARY KEY (id);


--
-- Name: sync_status sync_status_file_path_key; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_status
    ADD CONSTRAINT sync_status_file_path_key UNIQUE (file_path);


--
-- Name: sync_status sync_status_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sync_status
    ADD CONSTRAINT sync_status_pkey PRIMARY KEY (id);


--
-- Name: transcript_chunks transcript_chunks_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcript_chunks
    ADD CONSTRAINT transcript_chunks_pkey PRIMARY KEY (id);


--
-- Name: idx_analytics_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_created ON public.site_analytics USING btree (created_at DESC);


--
-- Name: idx_analytics_site; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_analytics_site ON public.site_analytics USING btree (site);


--
-- Name: idx_chunks_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_agent ON public.memory_chunks USING btree (agent_id);


--
-- Name: idx_chunks_source; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_chunks_source ON public.memory_chunks USING btree (source_file);


--
-- Name: idx_conv_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conv_agent ON public.conversation_history USING btree (agent_id);


--
-- Name: idx_conv_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conv_created ON public.conversation_history USING btree (created_at DESC);


--
-- Name: idx_conv_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conv_session ON public.conversation_history USING btree (session_key);


--
-- Name: idx_conversation_history_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_conversation_history_embedding_hnsw ON public.conversation_history USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_dm_conv_contact; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dm_conv_contact ON public.dm_conversations USING btree (contact_id);


--
-- Name: idx_dm_conv_created; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_dm_conv_created ON public.dm_conversations USING btree (created_at DESC);


--
-- Name: idx_facts_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facts_agent ON public.memory_facts USING btree (agent_id);


--
-- Name: idx_facts_category; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_facts_category ON public.memory_facts USING btree (category);


--
-- Name: idx_memory_chunks_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memory_chunks_embedding_hnsw ON public.memory_chunks USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_memory_facts_embedding_hnsw; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_memory_facts_embedding_hnsw ON public.memory_facts USING hnsw (embedding public.vector_cosine_ops);


--
-- Name: idx_st_agent; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_st_agent ON public.session_transcripts USING btree (agent_id);


--
-- Name: idx_st_archived; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_st_archived ON public.session_transcripts USING btree (archived_at DESC);


--
-- Name: idx_st_session; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_st_session ON public.session_transcripts USING btree (session_id);


--
-- Name: idx_transcript_chunks_embedding; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcript_chunks_embedding ON public.transcript_chunks USING ivfflat (embedding public.vector_cosine_ops) WITH (lists='100');


--
-- Name: idx_transcript_chunks_transcript_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcript_chunks_transcript_id ON public.transcript_chunks USING btree (transcript_id);


--
-- Name: idx_transcripts_agent_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcripts_agent_id ON public.conversation_transcripts USING btree (agent_id);


--
-- Name: idx_transcripts_archived_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcripts_archived_at ON public.conversation_transcripts USING btree (archived_at DESC);


--
-- Name: idx_transcripts_session_key; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcripts_session_key ON public.conversation_transcripts USING btree (session_key);


--
-- Name: idx_transcripts_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_transcripts_user_id ON public.conversation_transcripts USING btree (user_id);


--
-- Name: sdr_agent_files sdr_agent_files_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agent_files
    ADD CONSTRAINT sdr_agent_files_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.sdr_agents(id) ON DELETE CASCADE;


--
-- Name: sdr_agent_sales sdr_agent_sales_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_agent_sales
    ADD CONSTRAINT sdr_agent_sales_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.sdr_agents(id) ON DELETE CASCADE;


--
-- Name: sdr_cart_abandonments sdr_cart_abandonments_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_cart_abandonments
    ADD CONSTRAINT sdr_cart_abandonments_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.sdr_agents(id) ON DELETE CASCADE;


--
-- Name: sdr_channels sdr_channels_agent_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sdr_channels
    ADD CONSTRAINT sdr_channels_agent_id_fkey FOREIGN KEY (agent_id) REFERENCES public.sdr_agents(id) ON DELETE CASCADE;


--
-- Name: transcript_chunks transcript_chunks_transcript_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transcript_chunks
    ADD CONSTRAINT transcript_chunks_transcript_id_fkey FOREIGN KEY (transcript_id) REFERENCES public.conversation_transcripts(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


