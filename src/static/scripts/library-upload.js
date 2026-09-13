function libraryUpload(libId) {
  return {
    uploadOpen: false, uploading: false, dragDepth: 0, uploadMessage: '',
    async collect(entry, prefix = '') {
      if (entry.isFile) return [[prefix + entry.name, await new Promise((resolve, reject) => entry.file(resolve, reject))]];
      const reader = entry.createReader();
      const files = [];
      for (;;) {
        const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
        if (!batch.length) break;
        for (const child of batch) {
          files.push(...await this.collect(child, prefix + entry.name + '/'));
          if (files.length > 20000) throw new Error('Too many files in one upload.');
        }
      }
      return files;
    },
    async dropped(event) {
      this.dragDepth = 0;
      if (this.uploading) return;
      // Capture entries while the drop event still grants access to them.
      const entries = Array.from(event.dataTransfer.items || []).map(item => item.webkitGetAsEntry?.()).filter(Boolean);
      const fallback = Array.from(event.dataTransfer.files || []).map(file => [file.name, file]);
      try {
        let files = fallback;
        if (entries.length) {
          files = [];
          for (const entry of entries) files.push(...await this.collect(entry));
        }
        await this.upload(files);
      } catch (error) { this.uploadMessage = error.message; this.uploadOpen = true; }
    },
    async upload(files) {
      if (this.uploading || !files.length) return;
      this.uploading = true; this.uploadOpen = true; this.uploadMessage = 'Uploading and scanning…';
      try {
        const body = new FormData();
        for (const [name, file] of files) body.append('files', file, name);
        const response = await fetch(`/api/lib/${libId}/upload`, { method: 'POST', body });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || (response.status === 413 ? 'Upload exceeds the size limit.' : 'Upload failed.'));
        await this.$ajax(window.location.href, { target: 'breadcrumb main statusbar' });
      } catch (error) { this.uploadMessage = error.message; }
      finally { this.uploading = false; }
    }
  };
}
