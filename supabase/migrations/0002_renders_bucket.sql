-- Clippy Club: private storage bucket for final renders, proof frames and contact sheets.
-- Paths: renders/<clip_id>.mp4, proof/<clip_id>.jpg, sheets/<clip_id>.jpg
-- Private with no storage.objects policies: only the service role (pipeline) and the portal backend can read or write.
-- The portal hands out short-lived signed URLs for review; nothing is ever publicly listable.

insert into storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
values ('renders', 'renders', false, 52428800, array['video/mp4', 'image/jpeg', 'image/png'])
on conflict (id) do update
  set public = excluded.public,
      file_size_limit = excluded.file_size_limit,
      allowed_mime_types = excluded.allowed_mime_types;
