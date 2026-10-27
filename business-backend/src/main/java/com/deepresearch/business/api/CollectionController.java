package com.deepresearch.business.api;

import java.util.List;
import java.util.Map;
import com.deepresearch.business.security.WorkspaceAccess;
import com.deepresearch.business.security.WorkspaceIdentity;
import com.deepresearch.business.service.DocumentAccessService;
import org.springframework.jdbc.core.JdbcTemplate;
import org.springframework.security.core.annotation.AuthenticationPrincipal;
import org.springframework.security.oauth2.jwt.Jwt;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.web.bind.annotation.*;
import org.springframework.web.server.ResponseStatusException;
import org.springframework.http.HttpStatus;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;

@RestController
public class CollectionController {
    private final JdbcTemplate jdbc;
    private final WorkspaceAccess access;
    public CollectionController(JdbcTemplate jdbc,WorkspaceAccess access){this.jdbc=jdbc;this.access=access;}
    public record Collection(@NotBlank @Size(max=100) String name,@NotBlank @Pattern(regexp="workspace|restricted") String visibility){}
    public record Grant(@NotBlank @Size(max=128) String subject,@NotBlank @Pattern(regexp="read|write|none") String permission){}
    public record Assignment(@Size(max=80) String collectionId){}
    private WorkspaceIdentity identity(Jwt jwt,String workspace){return access.resolve(jwt,workspace);}
    private void lock(WorkspaceIdentity i){
        jdbc.queryForObject("SELECT id FROM workspaces WHERE id=? FOR UPDATE",String.class,i.workspaceId());
        List<Map<String,Object>> members=jdbc.queryForList("SELECT role FROM memberships WHERE workspace_id=? AND subject=?",i.workspaceId(),i.subject());
        if(members.isEmpty())throw new ResponseStatusException(HttpStatus.FORBIDDEN,"当前工作空间成员权限已撤销");
        WorkspaceIdentity current=new WorkspaceIdentity(i.workspaceId(),i.subject(),String.valueOf(members.getFirst().get("role")),i.displayName());
        access.require(current,"admin");
    }
    private void revision(WorkspaceIdentity i){
        jdbc.update("INSERT INTO workspace_corpus_revisions(workspace_id,revision) VALUES(?,1) ON CONFLICT(workspace_id) DO UPDATE SET revision=workspace_corpus_revisions.revision+1",i.workspaceId());
    }
    @GetMapping("/api/collections")
    List<Map<String,Object>> list(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace){
        var i=identity(jwt,workspace);
        return jdbc.queryForList("""
            SELECT c.* FROM collections c WHERE c.workspace_id=? AND
            (? OR c.visibility='workspace' OR c.created_by=? OR EXISTS(SELECT 1 FROM collection_grants g WHERE g.collection_id=c.id AND g.subject=?)) ORDER BY c.created_at,c.id""",
            i.workspaceId(),i.hasRole("admin"),i.subject(),i.subject());
    }
    @PostMapping("/api/collections") @Transactional
    Map<String,Object> create(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace,@Valid @RequestBody Collection body){
        var i=identity(jwt,workspace);lock(i);String id=WorkspaceAccess.uid();
        jdbc.update("INSERT INTO collections(id,workspace_id,name,visibility,created_by,created_at) VALUES(?,?,?,?,?,?)",id,i.workspaceId(),body.name(),body.visibility(),i.subject(),WorkspaceAccess.now());
        revision(i);access.audit(i,"collection.create","collection",id);return Map.of("id",id);
    }
    @PutMapping("/api/collections/{id}/grants") @Transactional
    Map<String,Boolean> grant(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace,@PathVariable String id,@Valid @RequestBody Grant body){
        var i=identity(jwt,workspace);lock(i);
        if(jdbc.queryForList("SELECT id FROM collections WHERE id=? AND workspace_id=?",id,i.workspaceId()).isEmpty()
            || jdbc.queryForList("SELECT subject FROM memberships WHERE workspace_id=? AND subject=?",i.workspaceId(),body.subject()).isEmpty())
            throw new ResponseStatusException(HttpStatus.NOT_FOUND,"集合或成员不存在");
        if("none".equals(body.permission()))jdbc.update("DELETE FROM collection_grants WHERE collection_id=? AND subject=?",id,body.subject());
        else jdbc.update("INSERT INTO collection_grants(collection_id,subject,permission) VALUES(?,?,?) ON CONFLICT(collection_id,subject) DO UPDATE SET permission=EXCLUDED.permission",id,body.subject(),body.permission());
        jdbc.update("UPDATE collections SET revision=revision+1 WHERE id=?",id);revision(i);
        access.audit(i,"collection.grant."+body.permission(),"collection",id);return Map.of("ok",true);
    }
    @PutMapping("/api/documents/{id}/collection") @Transactional
    Map<String,Boolean> assign(@AuthenticationPrincipal Jwt jwt,@RequestHeader(value="X-Workspace-ID",required=false)String workspace,@PathVariable String id,@Valid @RequestBody Assignment body){
        var i=identity(jwt,workspace);lock(i);
        if(jdbc.queryForList("SELECT id FROM documents WHERE id=? AND user_id=? AND status!='deleted'",id,i.workspaceId()).isEmpty())throw new ResponseStatusException(HttpStatus.NOT_FOUND,"资料不存在");
        String collection=body.collectionId()==null?"":body.collectionId();
        if(!collection.isEmpty() && jdbc.queryForList("SELECT id FROM collections WHERE id=? AND workspace_id=?",collection,i.workspaceId()).isEmpty())throw new ResponseStatusException(HttpStatus.NOT_FOUND,"集合不存在");
        jdbc.update("INSERT INTO document_policies(document_id,workspace_id,collection_id,owner_subject) VALUES(?,?,?,?) ON CONFLICT(document_id) DO UPDATE SET collection_id=EXCLUDED.collection_id,revision=document_policies.revision+1",id,i.workspaceId(),collection,i.subject());
        revision(i);access.audit(i,"document.collection","document",id);return Map.of("ok",true);
    }
}
